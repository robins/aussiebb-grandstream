---
name: aussiebb-grandstream
description: "Configure, fix or check a Grandstream SIP phone on Aussie Broadband (AussieBB, ABB) VoIP. Tested on a WP816; other Grandstream phones and ATAs (WP8xx, GRP, GXP, HT80x) use the same P-codes for most settings but are untested. Maps the settings Aussie BB sends (Proxy/Registrar/Outbound Proxy, Authentication Username/Password, Caller ID) onto Grandstream fields, gives the known-working P-code values, applies and checks them through the phone's restricted SSH shell (WP816> / CONFIG>) with a bundled script, and probes the Aussie BB server from a computer. Use this when a Grandstream on AussieBB shows \"Account registration failed\", when it is registered and makes outgoing calls but incoming callers hear busy, when someone asks what goes in SIP User ID vs Authenticate ID for AussieBB, or needs to get/set Grandstream P-values over SSH, even if they only say \"my AussieBB VoIP phone\" or mention nms1.voice.wide.net.au."
---

# Grandstream phone on Aussie Broadband VoIP

Aussie Broadband hands out its VoIP settings in its own terms (Proxy Server,
Registrar Server, Authentication Username, Caller ID ...). Grandstream uses
different field names, and its SSH access is a locked-down command shell
with quirks of its own. This skill holds the known-working mapping, the
complete set of values, a script that applies and checks them over SSH, and
a probe that tests the Aussie BB server from a computer.

Known working on a **Grandstream WP816, firmware 1.0.3.31**, on an Aussie
Broadband NBN service behind CGNAT, last checked **October 2026**. The
P-codes are standard Grandstream ones, so other Grandstream phones should
take the same values, but none has been tested. Their menus and SSH prompt
may look different, and older models may not have `26054`.

## What you need from the person

- **The phone's LAN address** (from the phone's status screen or the
  router's DHCP list).
- **A credentials file they write themselves.** Ask them to create a file
  outside this skill folder, for example `~/aussiebb.env`, and tell you its
  path:

  ```
  GS_PASSWORD=<phone admin password>
  SIP_USER=<Authentication Username>
  SIP_PASSWORD=<Authentication Password>
  SIP_NAME=<Caller ID Name>
  ```

  The SIP values are in the VoIP settings Aussie BB sends when the service
  is set up. Newer Grandstream phones ship with a unique admin password
  printed on the label. Both scripts take `--env-file <path>` and read what
  they need from it. Pass the path and don't open the file. That keeps the
  passwords out of the conversation.

  If the person would rather paste the values into the chat, write that
  file yourself, outside the skill folder, and delete it at the end.
- **Python 3 with `paramiko`** (`pip install paramiko`) for `gs_ssh.py`.
  `sip_probe.py` needs only the standard library.

Variables already set in the environment take precedence over the file. A
stale `SIP_PASSWORD` left in the shell will win.

## The mapping: most of the pain is here

Aussie BB's settings sheet looks like this:

```
Your new phone number is <10-digit phone number>
Proxy Server:            AussieBB.nms1.voice.wide.net.au
Registrar Server:        AussieBB.nms1.voice.wide.net.au
Outbound Proxy:          AussieBB.nms1.voice.wide.net.au
Port:                    5060
Authentication Username: <auth username>
Authentication Password: <auth password>
Caller ID Name:          <caller id name>
Caller ID Number:        <same as auth username>
```

| Aussie BB says | Grandstream field | P-code |
|---|---|---|
| Proxy Server / Registrar Server | SIP Server | `47` |
| Outbound Proxy | Outbound Proxy | `48` |
| Authentication Username | SIP User ID **and** Authenticate ID | `35`, `36` |
| Authentication Password | Authenticate Password | `34` |
| Caller ID Name | Name (display name) | `3` |
| Caller ID Number | no field of its own. It is the same value as the Authentication Username, already in SIP User ID | - |
| Port 5060 | Local SIP Port. The server side defaults to 5060, so don't append `:5060` to the server name | `40` |
| "Your new phone number" | **not entered anywhere** | - |

Three things catch people out:

1. **SIP User ID is the Authentication Username, not the phone number.** The
   10-digit number in the welcome message is what callers dial. It goes into
   no field on the phone.
2. **Type the server name exactly as Aussie BB writes it, capitals
   included:** `AussieBB.nms1.voice.wide.net.au`. The server's
   authentication realm is `AussieBB`, and the mixed-case form is the one
   known to work. Don't lowercase it.
3. **Put the same host in Outbound Proxy.** Leave Secondary SIP Server
   (`2312`) and Backup Outbound Proxy (`2333`) empty.

## Known-working settings (account 1)

| P-code | Setting | Value |
|---|---|---|
| `271` | Account Active | `1` (Yes) |
| `270` | Account Name | `AussieBB` (just a label) |
| `47` | SIP Server | `AussieBB.nms1.voice.wide.net.au` |
| `48` | Outbound Proxy | `AussieBB.nms1.voice.wide.net.au` |
| `35` | SIP User ID | `<auth username>` |
| `36` | Authenticate ID | `<auth username>` |
| `34` | Authenticate Password | `<auth password>` |
| `3` | Name | `<caller id name>` |
| `31` | SIP Registration | `1` (Yes) |
| `32` | Register Expiration (minutes) | `60` (the server grants 3600 s) |
| `40` | Local SIP Port | `5060` |
| `130` | SIP Transport | `1` (TCP). See below |
| `26054` | Use X-Grandstream-PBX Header | `0` (No). Required with TCP, see below |
| `103` | DNS Mode | `0` (A record) |
| `52` | NAT Traversal | `2` (Keep-alive) |
| `63` | SIP User ID is Phone Number | `0` (No) |
| `57` `58` `59` `60` | Preferred Vocoder 1-4 | `8` PCMA, `0` PCMU, `9` G.722, `123` Opus |
| `61` `62` `46` `98` | Preferred Vocoder 5-8 | `4` G.723.1, `18` G.729, `98` iLBC, `2` G.726-32 |

The working codec order puts G.711 A-law (PCMA, the Australian G.711
variant) first. Leave anything not listed here at its factory default.
`aussiebb.cmds` in this folder holds exactly this table.

### Why TCP, and why the X-Grandstream-PBX header must be off

Both were found on one Aussie BB NBN service behind CGNAT, in October 2026.
A service that has opted out of CGNAT, or has a static IP, hasn't been
tried. TCP should work there too.

- **UDP registrations from an Aussie BB connection are ignored.** Aussie BB's
  voice servers (nms1/nms2.voice.wide.net.au) never answered a UDP REGISTER
  carrying a Contact when it came from Aussie BB's own CGNAT addresses.
  OPTIONS and Contact-less REGISTERs from the same socket still got answers.
  The same packets reached other SIP providers fine, so the home network
  wasn't at fault. Aussie BB support said there was no IP block. Over
  **TCP**, the same registration gets a normal 407 → 200 OK. Their DNS also
  lists TCP 5060 (`_sip._tcp.nms1.voice.wide.net.au`). UDP did work from a
  mobile hotspot. TCP is the setting that works from both.
- **With TCP, P26054 must be 0, or incoming calls get "busy".** With "Use
  X-Grandstream-PBX Header" on (the factory default), the phone sends an
  OPTIONS with `Content-Type: application/x-grandstream-callfeatures+xml`
  straight after registering. That OPTIONS is for Grandstream's own UCM
  PBXs. The Aussie BB server never answers its authenticated retry. After
  32 s (the SIP transaction timeout) the phone closes the TCP connection
  its registration lives on. Outgoing calls still work, because each opens
  a new connection. Incoming calls have nowhere to go, and callers hear
  busy. On UDP the unanswered OPTIONS was harmless.

Change P130 or P26054 with `cfg_update` and then reboot. A live switch can
leave the old state in place until the phone restarts.

## Applying the settings over SSH

The phone's SSH login lands in a whitelisted command shell (`WP816>`), not a
real shell. `ssh phone some-command` does nothing useful, and piping a
script into `ssh` is unreliable. `scripts/gs_ssh.py` opens an interactive
channel, sends one line at a time, waits for the prompt after each, and
prints the whole exchange. It also:

- fills in `${NAME}` from the environment or `--env-file`, and stops before
  connecting if a name is missing,
- shows passwords as `********`, on screen and in any log,
- checks every `set N value` against the phone's `N = value` reply, and
  `commit` against `Changes are commited.` (the phone's spelling). It ends
  with `N set/commit check(s) passed`, or lists the problems and exits
  with status 1.

Each argument is one shell line. Quote any line that contains spaces.

### 1. Read what's there now

```bash
python <skill-dir>/scripts/gs_ssh.py <phone-ip> --env-file <env-file> status config "get 47" "get 48" "get 35" "get 36" "get 3" "get 130" "get 26054" "get 57" exit exit
```

`status` shows the model and firmware. Note them if they differ from the
WP816 / 1.0.3.31 baseline.

The script warns about an unknown host key each run, showing the key's
fingerprint. That is expected for a phone reached by IP. If the person runs
`ssh admin@<phone-ip>` once and accepts the key, later runs check it and
refuse a changed one.

### 2. Apply the known-working set

`aussiebb.cmds` holds every value in the table above, with `${SIP_USER}`,
`${SIP_PASSWORD}` and `${SIP_NAME}` in place of the person's details. It
needs no editing:

```bash
python <skill-dir>/scripts/gs_ssh.py <phone-ip> --env-file <env-file> -f <skill-dir>/aussiebb.cmds
```

Exit status 0 with `25 set/commit check(s) passed` means every value went
in and was written to flash. For a phone with settings that need to differ
from the table, copy the file outside the skill folder and edit the copy.

### 3. Reboot and read back

Reboot so the phone registers again from a clean start:

```bash
python <skill-dir>/scripts/gs_ssh.py <phone-ip> --env-file <env-file> reboot
```

The WP816 prints `Rebooting...` and drops the connection without asking
for confirmation. Power-cycling the phone works too. Once it's back (about
a minute), run the step 1 reads again and compare them to the table.

## Quirks of the restricted shell

- **Use bare numbers: `get 47`, not `get P47`.** The `P47` form, and
  dotted names like `account.1.sip.userid`, return nothing at all. The
  shell gives no error, so it looks like the setting is empty.
- **An empty `get` reply means one of three things**, and the shell won't
  say which: an unknown name, an empty value, or a write-only setting.
- **Passwords are write-only.** `get 34` comes back blank even right after
  `set 34`. That doesn't mean the password is missing. The `set 34` check
  in `gs_ssh.py` confirms the phone took it. If you doubt it, set it again.
- `config` enters the `CONFIG>` sub-shell. Its only commands are `set`,
  `get`, `unset` (clears a value), `commit` (writes to flash), `cfg_update`
  (applies) and `exit`. Run `commit` and then `cfg_update` after changes.
- `exit` twice: once to leave `CONFIG>`, once to disconnect.
- `otp_list` prints `OTP setting table not found!`. That's harmless.
- The top-level shell (`WP816>`) has only `config`, `status`, `wifi`,
  `upgrade`, `reboot`, `reset` (factory reset, so avoid it), `format`,
  `ping6`, `help` and `exit`.
- Caller ID names containing spaces haven't been tried over SSH. If the
  phone cuts one short, the `set 3` check fails. Set the name in the web UI
  instead.

## Doing it in the web UI instead

Log in at `http://<phone-ip>` as admin. On the WP816 the fields sit roughly
under **Account → Account 1**:

- **General Settings**: Account Active, Account Name, SIP Server, Outbound
  Proxy, SIP User ID, Authenticate ID, Authenticate Password, Name
- **Network Settings**: DNS Mode, NAT Traversal
- **SIP Settings**: SIP Registration, Register Expiration, Local SIP Port,
  SIP Transport
- **Codec Settings**: Preferred Vocoder 1-8

Menu layout differs between models and firmware versions. The P-code is the
reliable identifier, and SSH `get` will confirm what the web UI saved.

## Verifying

- **On the phone:** "Account registration failed" goes away and the
  account shows as registered. The web UI's account status page should say
  Registered.
- **Credentials and network path, independent of the phone:**
  `scripts/sip_probe.py` talks to the Aussie BB server from a computer on
  the same connection as the phone. Run it with `--contact`, once over UDP
  and once with `--tcp`. Nothing it sends adds or removes a registration,
  so it won't knock the phone off: its REGISTERs either carry no Contact (a
  query) or have their challenge left unanswered.

  ```bash
  python <skill-dir>/scripts/sip_probe.py AussieBB.nms1.voice.wide.net.au --env-file <env-file> --contact
  python <skill-dir>/scripts/sip_probe.py AussieBB.nms1.voice.wide.net.au --env-file <env-file> --contact --tcp
  ```

  Healthy UDP output (after a first line showing the resolved address):

  ```
  OPTIONS : SIP/2.0 407 Proxy Authentication Required
            Via as server saw it: SIP/2.0/UDP ...;received=<public address>;...
  CONTACT : SIP/2.0 407 Proxy Authentication Required
  REGISTER: SIP/2.0 407 Proxy Authentication Required  (Proxy-Authenticate: Digest realm="AussieBB", nonce="...")
  REGISTER: SIP/2.0 200 OK
            0 contact(s) currently registered
  ```

  - **Final `200 OK`**: server, username and password are correct. That is
    all it proves. The probe's own query carries no Contact, so it can
    pass while the phone's registrations are being ignored. `CONTACT`
    covers that case.
  - **`CONTACT` gets a 407**: phone-style registrations over that
    transport reach the server. If the phone still fails on the same
    transport, the problem is in its settings. Compare them with the table
    one field at a time.
  - **Over UDP, `CONTACT` gets no reply while `REGISTER` gets `200 OK`**:
    the server is ignoring UDP registrations from this connection. That is
    the normal result on an Aussie BB connection behind CGNAT. The `--tcp`
    run should then show a 407 for `CONTACT`, which is why the phone is set
    to TCP (`130` = `1`) and `26054` = `0`.
  - **With `--tcp`, no reply or `server closed the connection`**: TCP 5060
    isn't getting through from here. Check for a firewall rule on outbound
    TCP 5060.
  - **`received=`** is the public address the server sees. If it doesn't
    belong to the connection you meant to use, traffic is leaving another
    way. Sort that out before anything else.
  - **`403 Forbidden`, or a second 407 after the digest**: wrong username
    or password.
  - **No reply at all over UDP**: this computer isn't reaching the server.
    Check that the name resolves and that outbound UDP 5060 is allowed.
  - A 407 on OPTIONS and on `CONTACT` is normal. This server challenges
    every request, and getting a challenge back shows it is listening.
  - `0 contact(s)` is normal too. This server doesn't seem to list
    existing registrations in its reply, even while the phone is registered.
  - **`accepted without a challenge`** should never happen on Aussie BB.
    The probe removes its own registration again straight away and prints
    the result. Re-run it if that line doesn't end in `200 OK`.

How the server behaves, for anyone reading packet captures: Aussie BB's
voice platform answers as `NetSapiens SiPBx`. It challenges REGISTER with
**407 Proxy-Authenticate**, not 401, using realm `AussieBB` and MD5 digest
without qop, and grants `Expires: 3600`.

## If it still won't register

1. Read every value back with `get` and compare it with the table. The
   usual causes are a value in the wrong field (the phone number in SIP User
   ID) or a mistyped password. Password 34 can't be read back, so set it
   again rather than guessing.
2. Check `get 130` is `1` (TCP). On UDP, an Aussie BB connection gets
   no answer to the phone's registrations at all (see "Why TCP" above).
3. Run `sip_probe.py --contact`, over UDP and with `--tcp`, from a computer
   on the same connection as the phone. If everything gets an answer and
   every phone value matches, but the phone still can't register, contact
   Aussie Broadband support. They can see the registration attempts from
   their side.

## Registered, outgoing calls work, incoming calls get "busy"

Check `get 26054`. If it is `1`, set it to `0`, run `commit` and
`cfg_update`, then reboot. See "Why TCP" above for the reason. To confirm
on a packet capture of the phone's TCP connection to the server: after
the REGISTER's `200 OK`, a `CSeq: 11 OPTIONS` goes unanswered, and about 32 s
later the phone sends a FIN. With `26054` = `0`, no OPTIONS is sent and the
connection stays open. Incoming INVITEs then arrive over that connection.

Grandstream P-codes for this firmware are in the phone's own web UI. The
file `https://<phone-ip>/tl/tl.account.js` lists each account setting with
its `P` number. Read that file when a setting isn't in the table above.

## Afterwards

- Neither script logs anything unless given `--log`. A `gs_ssh.py` log has
  passwords masked. A `sip_probe.py` log holds digest responses, which can
  be brute-forced offline against a weak password. Keep both private, and
  never commit them.
- If you wrote the credentials file yourself, delete it. If the person
  wrote it, remind them it holds their passwords.
- If the admin password was shared during setup, change it in the web UI.
