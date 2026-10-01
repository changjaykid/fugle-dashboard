"""Encrypt a published JSON payload so a static host can serve it without publishing its contents.

Why not a password prompt in JavaScript. The site is GitHub Pages: `radar.json` answers HTTP 200 to
anyone with the URL, so a script that hides the page after the file has already been fetched protects
nothing, and the password would sit in the source it is meant to protect. Measured before building this:
`curl https://changjaykid.github.io/fugle-dashboard/radar.json` returned 200.

So the file itself is the thing that gets locked. AES-256-GCM, key from PBKDF2-SHA256 over the password,
and the browser does the derivation and the decryption with WebCrypto -- the password never reaches the
network and never appears in any published file. What is published is ciphertext plus the salt, the IV
and the iteration count, which are not secrets.

What this does and does not do. It stops search engines, crawlers, scrapers and anyone who merely has
the link: none of them can read the payload. It does not stop someone who decides to attack it, because
the strength is entirely the password's: eight digits is 10^8 candidates, and PBKDF2 slows a guess down
without making that number large. A passphrase would change that; the iteration count will not.

The password is read from a file outside the repository and is never written to one. Nothing here logs
it, echoes it, or puts it in the output.

    python3 tools/encrypt_payload.py --in docs/radar.json --out docs/radar.enc.json \
        --password-file ~/kid-agent-data/stock-radar/site-password
"""
import argparse
import base64
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

VERSION = 1
# OWASP's 2023 floor for PBKDF2-SHA256. About a second in a browser, paid once per session.
ITERATIONS = 600_000
SALT_BYTES = 16
IV_BYTES = 12   # AES-GCM's standard nonce length, which is what WebCrypto expects


def derive(password, salt, iterations=ITERATIONS):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations, dklen=32)


def encrypt(plaintext, password, iterations=ITERATIONS):
    """Returns the published envelope. The tag is appended to the ciphertext, as WebCrypto expects.

    Compressed first, because ciphertext cannot be compressed afterwards: the host gzips text responses,
    but base64 of AES output is incompressible, so skipping this would publish 5.2MB where 0.5MB will do
    and make every page load pay for it. The browser inflates it with DecompressionStream."""
    from Crypto.Cipher import AES
    body_in = gzip.compress(plaintext, 9)
    salt, iv = os.urandom(SALT_BYTES), os.urandom(IV_BYTES)
    cipher = AES.new(derive(password, salt, iterations), AES.MODE_GCM, nonce=iv)
    body, tag = cipher.encrypt_and_digest(body_in)
    b64 = lambda raw: base64.b64encode(raw).decode('ascii')
    return {'v': VERSION, 'kdf': 'PBKDF2-SHA256', 'cipher': 'AES-256-GCM', 'compression': 'gzip',
            'iterations': iterations, 'salt': b64(salt), 'iv': b64(iv), 'ct': b64(body + tag)}


def decrypt(envelope, password):
    """Only used to verify what was just written. The browser does this for real."""
    from Crypto.Cipher import AES
    if envelope.get('v') != VERSION or envelope.get('cipher') != 'AES-256-GCM':
        raise ValueError('unsupported_envelope')
    raw = base64.b64decode(envelope['ct'])
    body, tag = raw[:-16], raw[-16:]
    cipher = AES.new(derive(password, base64.b64decode(envelope['salt']), envelope['iterations']),
                     AES.MODE_GCM, nonce=base64.b64decode(envelope['iv']))
    out = cipher.decrypt_and_verify(body, tag)
    return gzip.decompress(out) if envelope.get('compression') == 'gzip' else out


def read_password(path):
    path = Path(path).expanduser()
    if not path.is_file():
        raise SystemExit(f'找不到密碼檔 {path}；請自行建立並設為 chmod 600。')
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise SystemExit(f'密碼檔 {path} 權限是 {mode:o}，其他使用者可讀；請 chmod 600 後再試。')
    password = path.read_text().strip()
    if not password:
        raise SystemExit('密碼檔是空的。')
    return password


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--in', dest='source', type=Path, required=True)
    p.add_argument('--out', dest='target', type=Path, required=True)
    p.add_argument('--password-file', required=True)
    p.add_argument('--iterations', type=int, default=ITERATIONS)
    a = p.parse_args(argv)
    plaintext = a.source.read_bytes()
    envelope = encrypt(plaintext, read_password(a.password_file), a.iterations)
    # Never publish an envelope that has not been opened again: a payload nobody can decrypt is worse
    # than one that was never locked, because the failure only shows up when someone needs to read it.
    if decrypt(envelope, read_password(a.password_file)) != plaintext:
        raise SystemExit('加密後驗證失敗，沒有寫出任何檔案。')
    tmp = a.target.with_suffix(a.target.suffix + '.tmp')
    tmp.write_text(json.dumps(envelope, separators=(',', ':')))
    os.replace(tmp, a.target)
    print(json.dumps({'status': 'encrypted', 'out': str(a.target),
                      'plaintext_bytes': len(plaintext), 'envelope_bytes': a.target.stat().st_size,
                      'iterations': a.iterations}, ensure_ascii=False))


if __name__ == '__main__':
    sys.exit(main())
