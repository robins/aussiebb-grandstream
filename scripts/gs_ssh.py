#!/usr/bin/env python3
"""Run commands in a Grandstream phone's restricted SSH shell.

The phone's SSH login lands in a whitelisted command shell (WP816>), not a
real shell, so `ssh phone cmd` does nothing useful. This opens an
interactive channel, sends one command at a time, waits for the next prompt
and prints the exchange.

Usage:
    python gs_ssh.py HOST CMD [CMD ...]
    python gs_ssh.py HOST -f commands.txt [--env-file FILE]

In a commands file, blank lines and lines starting with ';' are skipped.

${NAME} in a command is replaced with NAME from the environment or, for
names the environment doesn't set, from --env-file (NAME=value lines). The
script stops before connecting if any of them is missing or empty.

Passwords are shown as ******** on screen and in the log: the values of
variables whose name contains PASSWORD, and anything sent to the password
P-codes 2 (admin), 34 (account 1) and 196 (user).

Each `set N value` is checked against the phone's `N = value` reply, and
each `commit` against "Changes are commited." (the phone's spelling). The
exit status is 1 if a check fails or the connection closes early.

The admin password comes from GS_PASSWORD (environment or --env-file). It is
prompted for only when run from a terminal. The user defaults to admin.
--log FILE appends the masked exchange to FILE. Nothing is logged otherwise.

A phone already in ~/.ssh/known_hosts must present the same host key. An
unknown key is accepted with a warning showing its fingerprint; run
`ssh admin@PHONE` once to pin it.
"""
import argparse
import base64
import datetime
import getpass
import hashlib
import os
import re
import sys
import time

import paramiko

# The shell's prompt is one word ending in '>' or '#': "WP816> ", "CONFIG> ".
PROMPT = re.compile(rb'^[\w-]+[>#] ?$')
# P-codes that hold passwords: admin, account 1 SIP, end user.
SECRET_PCODES = {'2', '34', '196'}
VAR = re.compile(r'\$\{(\w+)\}')
SET = re.compile(r'set\s+(\d+)\s+(.*)$')
COMMITTED = re.compile(r'Changes are commit+ed')
MASK = '********'


def load_env_file(path):
    """Read NAME=value lines. Blank lines and '#' comments are skipped."""
    values = {}
    with open(os.path.expanduser(path), encoding='utf-8') as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith('#'):
                continue
            name, sep, value = ln.partition('=')
            if not sep:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                value = value[1:-1]
            values[name.strip()] = value
    return values


def expand(commands, get):
    """Fill in each ${NAME}, and collect the values that must not be shown."""
    names = {n for c in commands for n in VAR.findall(c)}
    missing = sorted(n for n in names if not get(n))
    if missing:
        sys.exit('not set in the environment or --env-file: ' + ', '.join(missing))
    secrets = {get(n) for n in names if 'PASSWORD' in n.upper()}
    out = []
    for c in commands:
        c = VAR.sub(lambda m: get(m.group(1)), c)
        m = SET.match(c.strip())
        if m and m.group(1) in SECRET_PCODES:
            secrets.add(m.group(2).strip())
        out.append(c)
    return out, {s for s in secrets if s}


def masker(secrets):
    """Return a function that replaces every secret in a string with MASK."""
    ordered = sorted(secrets, key=len, reverse=True)  # longest first

    def mask(text):
        for s in ordered:
            text = text.replace(s, MASK)
        return text
    return mask


def check(cmd, reply):
    """Check the phone's reply to a `set` or `commit`.

    Returns None for commands that aren't checked, '' when the reply confirms
    the command, or what is wrong with it.
    """
    cmd = cmd.strip()
    m = SET.match(cmd)
    if m:
        code, want = m.group(1), m.group(2).strip()
        got = re.search(rf'^{code} = (.*?)\s*$', reply, re.M)
        if not got:
            return f'no "{code} = ..." in the reply'
        if got.group(1) != want:
            return f'phone replied "{code} = {got.group(1)}"'
        return ''
    if cmd == 'commit':
        return '' if COMMITTED.search(reply) else 'no "Changes are commited." in the reply'
    return None


class WarnUnknownKey(paramiko.MissingHostKeyPolicy):
    """Accept a host key that isn't in known_hosts, but say so."""

    def missing_host_key(self, client, hostname, key):
        # The same SHA256 form `ssh` shows, so the two can be compared.
        fp = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        print(f'warning: {hostname} is not in known_hosts. Accepting its '
              f'{key.get_name()} key SHA256:{fp}', file=sys.stderr)


def show(text):
    sys.stdout.write(text.replace('\r\n', '\n').replace('\r', '\n'))
    sys.stdout.flush()


def log_pair(path, sent, received):
    """Append one command/response pair to the log, if there is one."""
    if not path:
        return
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(path, 'a', encoding='utf-8', newline='\n') as fh:
        fh.write('\n\n\n\n')
        fh.write(f'[{stamp}] SENT: {sent}\n')
        fh.write(f'[{stamp}] RECEIVED:\n')
        fh.write(received.replace('\r\n', '\n').replace('\r', '\n'))
        fh.write('\n')


def read_until_prompt(chan, timeout=20.0, settle=0.4, idle=4.0):
    """Read until a prompt has been seen and the line has gone quiet.

    Falls back to returning after `idle` seconds of silence, so an unknown
    prompt (a y/N question, say) does not hang the script.
    """
    buf = b''
    start = last = time.monotonic()
    while time.monotonic() - start < timeout:
        if chan.recv_ready():
            buf += chan.recv(65536)
            last = time.monotonic()
            continue
        if chan.closed or chan.exit_status_ready():
            break
        quiet = time.monotonic() - last
        tail = buf.rsplit(b'\n', 1)[-1]
        if PROMPT.search(tail) and quiet >= settle:
            break
        if buf and quiet >= idle:
            break
        time.sleep(0.05)
    return buf.decode('utf-8', 'replace')


def main():
    # Phone output is decoded with errors='replace'. Don't let one odd
    # character crash printing to a console or pipe that isn't UTF-8.
    sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('host')
    ap.add_argument('commands', nargs='*')
    ap.add_argument('-f', '--file', help='read commands from a file, one per line')
    ap.add_argument('--env-file', help='NAME=value lines for ${NAME} and GS_PASSWORD')
    ap.add_argument('--user', default='admin')
    ap.add_argument('--port', type=int, default=22)
    ap.add_argument('--timeout', type=float, default=20.0,
                    help='max seconds to wait for each command (default 20)')
    ap.add_argument('--log', help='append the masked exchange to this file')
    args = ap.parse_args()

    commands = list(args.commands)
    if args.file:
        with open(os.path.expanduser(args.file), encoding='utf-8') as fh:
            commands += [ln.rstrip('\r\n') for ln in fh
                         if ln.strip() and not ln.lstrip().startswith(';')]
    if not commands:
        ap.error('no commands given')

    from_file = load_env_file(args.env_file) if args.env_file else {}

    def get(name):
        return os.environ.get(name) or from_file.get(name, '')

    commands, secrets = expand(commands, get)
    mask = masker(secrets)
    password = get('GS_PASSWORD')
    if not password:
        # Only prompt a person at a terminal. Checking stdout as well matters
        # on Windows, where a NUL stdin claims to be a tty and getpass would
        # wait on the console forever.
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            sys.exit('set GS_PASSWORD, in the environment or --env-file, '
                     'to the phone admin password')
        password = getpass.getpass(f'{args.user}@{args.host} password: ')

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(WarnUnknownKey())
    client.connect(args.host, port=args.port, username=args.user, password=password,
                   look_for_keys=False, allow_agent=False, timeout=15)
    checked, problems = 0, []
    try:
        chan = client.invoke_shell(width=200, height=1000)
        banner = read_until_prompt(chan, args.timeout)
        log_pair(args.log, f'(ssh login {args.user}@{args.host})', banner)
        show(banner)
        for i, cmd in enumerate(commands):
            if chan.closed or chan.exit_status_ready():
                problems.append(f'connection closed with {len(commands) - i} command(s) '
                                f'not sent, starting at {mask(cmd)!r}')
                break
            chan.send(cmd + '\n')
            reply = read_until_prompt(chan, args.timeout)
            log_pair(args.log, mask(cmd), mask(reply))
            show(mask(reply))
            problem = check(cmd, reply)
            if problem is not None:
                checked += 1
                if problem:
                    problems.append(f'{mask(cmd.strip())}: {mask(problem)}')
    finally:
        client.close()
    sys.stdout.write('\n')
    if problems:
        print(f'{len(problems)} problem(s):', file=sys.stderr)
        for p in problems:
            print(f'  {p}', file=sys.stderr)
        sys.exit(1)
    if checked:
        print(f'{checked} set/commit check(s) passed')


if __name__ == '__main__':
    main()
