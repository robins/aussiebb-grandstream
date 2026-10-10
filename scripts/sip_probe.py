#!/usr/bin/env python3
"""Probe a SIP registrar from this machine, without registering anything.

Sends these over UDP, or over one TCP connection with --tcp:
  1. OPTIONS, no credentials -- is the server reachable from this network?
  2. With --contact: one REGISTER carrying a Contact header, the way a phone
     sends it. Its challenge is not answered, so nothing is registered. A
     reply means phone registrations get through from this connection;
     silence while 1 and 3 succeed means the server is ignoring them. If a
     server ever accepts it without a challenge, the binding is taken out
     again straight away with Expires: 0, and the output says so.
  3. REGISTER with no Contact header -- a binding *query*. The server
     challenges (401/407), this answers with digest auth, and a 200 OK lists
     the contacts currently registered for the account. Nothing is added or
     removed.

Usage:
    python sip_probe.py SERVER [USER] [--auth-user AUTH] [--contact] [--tcp]
                        [--port 5060] [--sport 0] [--env-file FILE] [--log FILE]

USER defaults to SIP_USER, and the password comes from SIP_PASSWORD, each
read from the environment or else from --env-file (NAME=value lines). The
password is prompted for only when run from a terminal. It is never sent in
clear, only as a digest.

--log FILE appends every message sent and received to FILE. Nothing is
logged otherwise. The log holds the digest responses, and an MD5 digest
without qop (what Aussie BB uses) can be brute-forced offline if the
password is weak, so keep the log private.
"""
import argparse
import datetime
import getpass
import hashlib
import os
import re
import secrets
import socket
import string
import sys
import time

ALPHABET = string.ascii_lowercase + string.digits


def rand(n=10):
    return ''.join(secrets.choice(ALPHABET) for _ in range(n))


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


def log_pair(path, dest, sent, src, received):
    """Append one sent message and whatever came back to the log, if there is one."""
    if not path:
        return
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(path, 'a', encoding='utf-8', newline='\n') as fh:
        fh.write('\n\n\n\n')
        fh.write(f'[{stamp}] SENT to {dest}:\n')
        fh.write(sent.replace('\r\n', '\n'))
        fh.write(f'[{stamp}] RECEIVED{" from " + src if src else ""}:\n')
        fh.write(received.replace('\r\n', '\n'))
        fh.write('\n')


def status_of(resp):
    m = re.match(r'SIP/2\.0 (\d{3})', resp)
    return int(m.group(1)) if m else 0


def headers(resp, *names):
    """All values of the named headers (case-insensitive)."""
    want = {n.lower() for n in names}
    out = []
    for line in resp.split('\r\n')[1:]:
        if not line:
            break
        name, _, value = line.partition(':')
        if name.strip().lower() in want:
            out.append(value.strip())
    return out


class Udp:
    """Plain UDP, retransmitting on silence the way a phone does."""
    proto = 'UDP'
    uri_param = ''
    closed = False

    def __init__(self, addr, sport):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(addr)
        lhost = probe.getsockname()[0]
        probe.close()
        self.addr = addr
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((lhost, sport))
        self.lhost, self.lport = self.sock.getsockname()[:2]

    def no_reply(self):
        return 'no reply'

    def transact(self, msg, log, timeout=2.0, tries=3):
        """Send msg, retransmitting on silence; return responses up to a final one."""
        dest = f'{self.addr[0]}:{self.addr[1]}'
        for attempt in range(1, tries + 1):
            self.sock.sendto(msg.encode(), self.addr)
            got, src = [], ''
            self.sock.settimeout(timeout)
            try:
                while True:
                    data, frm = self.sock.recvfrom(65535)
                    got.append(data.decode('utf-8', 'replace'))
                    src = f'{frm[0]}:{frm[1]}'
                    if status_of(got[-1]) >= 200:
                        break
            except socket.timeout:
                pass
            received = '\n'.join(got) if got else f'(no reply within {timeout:g}s, attempt {attempt}/{tries})'
            log_pair(log, dest, msg, src, received)
            if got:
                return got
        return []


class Tcp:
    """One TCP connection carrying every request, as a phone keeps one open."""
    proto = 'TCP'
    uri_param = ';transport=tcp'

    def __init__(self, addr, sport):
        self.addr = addr
        self.sock = socket.create_connection(addr, timeout=10,
                                             source_address=('', sport) if sport else None)
        self.lhost, self.lport = self.sock.getsockname()[:2]
        self.buf = b''
        self.closed = False

    def no_reply(self):
        return 'no reply (server closed the connection)' if self.closed else 'no reply'

    def _message(self, deadline):
        """Return the next complete SIP message off the stream, or None by the deadline."""
        while True:
            self.buf = self.buf.lstrip(b'\r\n')  # keep-alive CRLFs between messages
            head, sep, rest = self.buf.partition(b'\r\n\r\n')
            if sep:
                m = re.search(rb'^(?:Content-Length|l)[ \t]*:[ \t]*(\d+)', head, re.I | re.M)
                size = int(m.group(1)) if m else 0
                if len(rest) >= size:
                    self.buf = rest[size:]
                    return (head + sep + rest[:size]).decode('utf-8', 'replace')
            left = deadline - time.monotonic()
            if left <= 0 or self.closed:
                return None
            self.sock.settimeout(left)
            try:
                data = self.sock.recv(65535)
            except socket.timeout:
                return None
            if not data:
                self.closed = True
                return None
            self.buf += data

    def transact(self, msg, log, timeout=6.0):
        """Send msg; return responses up to a final one. TCP needs no retransmits."""
        dest = f'{self.addr[0]}:{self.addr[1]}'
        got, note = [], f'(no reply within {timeout:g}s)'
        try:
            self.sock.sendall(msg.encode())
            deadline = time.monotonic() + timeout
            while (resp := self._message(deadline)) is not None:
                if not resp.startswith('SIP/2.0 '):
                    continue  # a request from the server, not an answer to ours
                got.append(resp)
                if status_of(resp) >= 200:
                    break
        except OSError as e:
            self.closed = True
            note = f'({e})'
        if self.closed and not got:
            note = '(server closed the connection)'
        log_pair(log, dest, msg, dest if got else '', '\n'.join(got) if got else note)
        return got


def build(method, uri, cseq, call_id, from_tag, user, domain, link, extra=()):
    to = f'<sip:{user}@{domain}>' if method == 'REGISTER' else f'<sip:{domain}>'
    lines = [f'{method} {uri} SIP/2.0',
             f'Via: SIP/2.0/{link.proto} {link.lhost}:{link.lport};branch=z9hG4bK{rand(12)};rport',
             'Max-Forwards: 70',
             f'From: <sip:{user}@{domain}>;tag={from_tag}',
             f'To: {to}',
             f'Call-ID: {call_id}',
             f'CSeq: {cseq} {method}',
             *extra,
             'User-Agent: sip-probe',
             'Content-Length: 0', '', '']
    return '\r\n'.join(lines)


def digest(challenge, user, password, method, uri):
    """Build a Digest Authorization value for a WWW/Proxy-Authenticate challenge."""
    ch = {k.lower(): v.strip('"') for k, v in
          re.findall(r'(\w+)\s*=\s*("[^"]*"|[^\s,]+)', challenge.partition(' ')[2])}
    algo = ch.get('algorithm', 'MD5')
    hashfn = {'MD5': hashlib.md5, 'SHA-256': hashlib.sha256}.get(algo.upper())
    if hashfn is None:
        raise SystemExit(f'unsupported digest algorithm: {algo}')

    def H(s):
        return hashfn(s.encode()).hexdigest()

    realm, nonce = ch.get('realm', ''), ch.get('nonce', '')
    ha1, ha2 = H(f'{user}:{realm}:{password}'), H(f'{method}:{uri}')
    parts = [f'username="{user}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"']
    if 'qop' in ch:
        qops = [q.strip() for q in ch['qop'].split(',')]
        qop = 'auth' if 'auth' in qops else qops[0]
        nc, cnonce = '00000001', rand(16)
        parts += [f'response="{H(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")}"',
                  f'cnonce="{cnonce}"', f'nc={nc}', f'qop={qop}']
    else:
        parts.append(f'response="{H(f"{ha1}:{nonce}:{ha2}")}"')
    parts.append(f'algorithm={algo}')
    if 'opaque' in ch:
        parts.append(f'opaque="{ch["opaque"]}"')
    return 'Digest ' + ', '.join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('server', help='SIP domain/registrar, case as the provider gives it')
    ap.add_argument('user', nargs='?', help='SIP user ID (default: SIP_USER)')
    ap.add_argument('--auth-user', help='authentication ID (default: USER)')
    ap.add_argument('--port', type=int, default=5060)
    ap.add_argument('--sport', type=int, default=0, help='local port (default: any)')
    ap.add_argument('--tcp', action='store_true',
                    help='send everything over one TCP connection instead of UDP')
    ap.add_argument('--contact', action='store_true',
                    help='also send one phone-style REGISTER with a Contact (registers nothing)')
    ap.add_argument('--env-file', help='NAME=value lines for SIP_USER and SIP_PASSWORD')
    ap.add_argument('--log', help='append every message sent and received to this file')
    args = ap.parse_args()

    from_file = load_env_file(args.env_file) if args.env_file else {}

    def get(name):
        return os.environ.get(name) or from_file.get(name, '')

    user = args.user or get('SIP_USER')
    if not user:
        ap.error('give USER, or set SIP_USER in the environment or --env-file')
    auth_user = args.auth_user or user
    password = get('SIP_PASSWORD')
    if not password:
        # Only prompt a person at a terminal. Checking stdout as well matters
        # on Windows, where a NUL stdin claims to be a tty and getpass would
        # wait on the console forever.
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            sys.exit('set SIP_PASSWORD, in the environment or --env-file')
        password = getpass.getpass('SIP password: ')

    domain = args.server
    uri = f'sip:{domain}'
    ip = socket.gethostbyname(domain)
    addr = (ip, args.port)
    try:
        link = Tcp(addr, args.sport) if args.tcp else Udp(addr, args.sport)
    except OSError as e:
        sys.exit(f'{domain} -> {ip}:{args.port} over {"TCP" if args.tcp else "UDP"}: {e}')
    print(f'{domain} -> {ip}:{args.port} over {link.proto}, local {link.lhost}:{link.lport}')

    def dialog():
        """A fresh Call-ID and From tag."""
        return f'{rand(16)}@{link.lhost}', rand()

    # 1. OPTIONS
    call_id, tag = dialog()
    opt = build('OPTIONS', uri, 1, call_id, tag, user, domain, link,
                extra=['Accept: application/sdp'])
    got = link.transact(opt, args.log)
    if got:
        final = got[-1]
        print(f'OPTIONS : {final.splitlines()[0]}')
        for via in headers(final, 'Via', 'v'):
            print(f'          Via as server saw it: {via}')
    else:
        print(f'OPTIONS : {link.no_reply()}')

    # 2. REGISTER with Contact, as a phone sends it; the challenge is not answered
    if args.contact:
        call_id, tag = dialog()
        contact = f'Contact: <sip:{user}@{link.lhost}:{link.lport}{link.uri_param}>'
        reg = build('REGISTER', uri, 1, call_id, tag, user, domain, link,
                    extra=[contact, 'Expires: 3600'])
        got = link.transact(reg, args.log)
        if not got:
            why = '' if link.closed else ' -- REGISTERs carrying a Contact are being ignored'
            print(f'CONTACT : {link.no_reply()}{why}')
        else:
            print(f'CONTACT : {got[-1].splitlines()[0]}')
            if 200 <= status_of(got[-1]) < 300:
                # Accepted without a challenge: take the binding straight back out.
                undo = build('REGISTER', uri, 2, call_id, tag, user, domain, link,
                             extra=[contact, 'Expires: 0'])
                got = link.transact(undo, args.log)
                result = got[-1].splitlines()[0] if got else link.no_reply()
                print('          accepted without a challenge, so this probe registered.')
                print(f'          Removed it again with Expires: 0: {result}')

    # 3. REGISTER query (no Contact)
    call_id, tag = dialog()
    reg = build('REGISTER', uri, 1, call_id, tag, user, domain, link)
    got = link.transact(reg, args.log)
    if not got:
        print(f'REGISTER: {link.no_reply()}')
        return
    final = got[-1]
    code = status_of(final)
    if code in (401, 407):
        hdr = 'WWW-Authenticate' if code == 401 else 'Proxy-Authenticate'
        auth = 'Authorization' if code == 401 else 'Proxy-Authorization'
        challenges = headers(final, hdr)
        if not challenges:
            print(f'REGISTER: {final.splitlines()[0]}  (no {hdr} header to answer)')
            return
        challenge = challenges[0]
        print(f'REGISTER: {final.splitlines()[0]}  ({hdr}: {challenge})')
        reg = build('REGISTER', uri, 2, call_id, tag, user, domain, link,
                    extra=[f'{auth}: {digest(challenge, auth_user, password, "REGISTER", uri)}'])
        got = link.transact(reg, args.log)
        if not got:
            print(f'REGISTER (with auth): {link.no_reply()}')
            return
        final = got[-1]
    print(f'REGISTER: {final.splitlines()[0]}')
    contacts = headers(final, 'Contact', 'm')
    if status_of(final) == 200:
        print(f'          {len(contacts)} contact(s) currently registered')
    for c in contacts:
        print(f'          Contact: {c}')


if __name__ == '__main__':
    main()
