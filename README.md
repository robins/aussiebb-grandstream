# aussiebb-grandstream

Getting a Grandstream SIP phone working on Aussie Broadband (AussieBB)
VoIP: the settings, two fixes I couldn't find written down anywhere, and
the scripts used to find them. It's packaged as an AI agent skill, but
everything here works by hand too.

Tested on a **Grandstream WP816 (firmware 1.0.3.31)** on an AussieBB NBN
service behind CGNAT, **October 2026**.

Unofficial. Not affiliated with Aussie Broadband or Grandstream.

## If you're here from a search

| Symptom | Fix |
|---|---|
| "Account registration failed", although the username and password are right | Set **SIP Transport** to **TCP** (P-code `130` = `1`) |
| Registered and outgoing calls work, but incoming callers hear busy | Set **Use X-Grandstream-PBX Header** to **No** (P-code `26054` = `0`) |
| "Account registration failed", and the phone number is in SIP User ID | Put the **Authentication Username** in both **SIP User ID** and **Authenticate ID**. The phone number isn't entered anywhere |

Reboot the phone after changing either of the first two. A live change can
leave the old behaviour in place until it restarts.

Both settings are in the account's settings in the phone's web UI
(Account → Account 1 on the WP816). Menus move between models and
firmware, and the P-code is the reliable way to find a setting.
[SKILL.md](SKILL.md) has the full field mapping and every known-working
value.

### Why

- **UDP registrations were ignored from AussieBB's own network.**
  AussieBB's voice servers never answered a UDP REGISTER carrying a
  Contact header when it came from AussieBB's CGNAT addresses. OPTIONS,
  and REGISTERs without a Contact, from the same socket were answered.
  The same packets got answers from other SIP providers, AussieBB support
  found no block on the connection, and UDP worked from a mobile hotspot.
  Over TCP, the same registration gets a normal 407 then 200 OK.
- **The X-Grandstream-PBX header option breaks TCP.** With it on (the
  factory default), the phone sends an OPTIONS meant for Grandstream's own
  UCM PBXs straight after registering. AussieBB never answers it, so after
  32 seconds the phone closes the TCP connection its registration lives
  on. Outgoing calls open their own connection and work. Incoming calls
  have nowhere to go, and callers hear busy.

**Not tested:** an AussieBB service that has opted out of CGNAT or has a
static IP, any Grandstream model other than the WP816, and other firmware.
If you try one, an issue saying what happened will help the next person.

The full story, with packet captures and red herrings, is in
[the blog post](BLOG-URL).

## What's here

| File | What it is |
|---|---|
| [SKILL.md](SKILL.md) | The full reference: field mapping, every P-code value, the SSH shell's quirks, troubleshooting. Written for an AI agent, readable by people |
| [aussiebb.cmds](aussiebb.cmds) | The known-working settings as a command file for `gs_ssh.py` |
| [scripts/gs_ssh.py](scripts/gs_ssh.py) | Drives the phone's restricted SSH shell (`WP816>` / `CONFIG>`) and checks every value it sets. Needs `paramiko` |
| [scripts/sip_probe.py](scripts/sip_probe.py) | Tests the AussieBB server from a computer on the same connection, over UDP or TCP, without registering anything. Standard library only |

Python 3 (tested with 3.12).

## Using it with an AI agent

Clone it into the agent's skills folder. For Claude Code:

```bash
git clone https://github.com/robins/aussiebb-grandstream ~/.claude/skills/aussiebb-grandstream
```

or into `.claude/skills/` inside a project. Then ask about your AussieBB
phone. The skill loads when the subject comes up. Other agents that read
`SKILL.md` files can use it from wherever they look for skills.

**Keep your passwords out of the chat.** Anything you paste into a
conversation goes to the model provider. Put the credentials in a file
outside the skill folder instead, and tell the agent its path:

```
GS_PASSWORD=<phone admin password>
SIP_USER=<Authentication Username>
SIP_PASSWORD=<Authentication Password>
SIP_NAME=<Caller ID Name>
```

Both scripts read it with `--env-file`, so the agent never needs to see
what's in it. Environment variables with the same names take precedence
over the file.

## Using it by hand

```bash
pip install paramiko

# What the phone has now
python scripts/gs_ssh.py 192.168.1.50 --env-file ~/aussiebb.env status config "get 47" "get 130" "get 26054" exit exit

# Apply the known-working settings. Every value is checked; exit status 1 if one didn't take
python scripts/gs_ssh.py 192.168.1.50 --env-file ~/aussiebb.env -f aussiebb.cmds

# Reboot
python scripts/gs_ssh.py 192.168.1.50 --env-file ~/aussiebb.env reboot

# Test the server from a computer on the same connection, over UDP and then TCP
python scripts/sip_probe.py AussieBB.nms1.voice.wide.net.au --env-file ~/aussiebb.env --contact
python scripts/sip_probe.py AussieBB.nms1.voice.wide.net.au --env-file ~/aussiebb.env --contact --tcp
```

[SKILL.md](SKILL.md#verifying) explains what the probe's output means.

## Security notes

- `gs_ssh.py` shows passwords as `********`, on screen and in its log.
  Neither script writes a log unless you pass `--log`.
- A `sip_probe.py` log holds digest responses. AussieBB uses MD5 digest
  without qop, which can be brute-forced offline against a weak password,
  so keep any log private.
- `gs_ssh.py` accepts a host key it hasn't seen before, with a warning
  showing the fingerprint. To pin it, run `ssh admin@<phone-ip>` once and
  accept the key. After that, a changed key is refused.
- The probe's phone-style REGISTER has its challenge left unanswered, so it
  registers nothing. If a server ever accepted one without a challenge, the
  probe removes that registration again immediately and says so.

## Licence

MIT. See [LICENSE](LICENSE).
