"""Offline EML import. No network dependencies."""
import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS emails (
 id TEXT PRIMARY KEY, source_path TEXT NOT NULL UNIQUE, source_folder TEXT NOT NULL,
 source_sha256 TEXT NOT NULL, message_id TEXT, date_raw TEXT, date_utc TEXT,
 sender TEXT, recipients TEXT, subject TEXT, body TEXT, body_type TEXT,
 in_reply_to TEXT, original_path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attachments (
 sha256 TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL, stored_path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS email_attachments (
 id TEXT PRIMARY KEY, email_id TEXT NOT NULL REFERENCES emails(id),
 attachment_sha256 TEXT NOT NULL REFERENCES attachments(sha256),
 part_index INTEGER NOT NULL, original_filename TEXT, mime_type TEXT,
 UNIQUE(email_id, part_index));
'''

def digest(data):
    return hashlib.sha256(data).hexdigest()

def parse(raw):
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    if not any(msg.get(k) for k in ('From', 'To', 'Subject', 'Message-ID', 'Date')):
        raise ValueError('MissingMailHeaders')
    issues = []
    body = {'text/plain': [], 'text/html': []}
    files = []
    def visit(part, key='0'):
        name = part.get_filename()
        if name is not None or part.get_content_disposition() == 'attachment':
            if part.is_multipart():
                raise ValueError('UnsupportedMultipartAttachment')
            payload = part.get_payload(decode=True)
            if payload is None:
                raise ValueError('UndecodableAttachment')
            files.append((key, name or '', part.get_content_type(), payload))
        elif part.is_multipart():
            for i, child in enumerate(part.iter_parts()):
                visit(child, key + '.' + str(i))
        elif part.get_content_type() in body:
            payload = part.get_payload(decode=True) or b''
            charset = part.get_content_charset() or 'utf-8'
            try:
                value = payload.decode(charset)
            except (UnicodeError, LookupError):
                issues.append('BodyDecodeFallback')
                value = payload.decode('utf-8', errors='replace')
            body[part.get_content_type()].append(value)
        issues.extend(type(d).__name__ for d in part.defects)
    visit(msg)
    date_raw = str(msg.get('Date', ''))
    utc = None
    if date_raw:
        try:
            dt = parsedate_to_datetime(date_raw)
            if dt.tzinfo is None:
                issues.append('MissingDateTimezone')
            else:
                utc = dt.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError, OverflowError):
            issues.append('InvalidDate')
    else:
        issues.append('MissingDate')
    kind = 'text/plain' if body['text/plain'] else 'text/html'
    fields = (str(msg.get('Message-ID', '')), date_raw, utc,
              str(msg.get('From', '')), str(msg.get('To', '')),
              str(msg.get('Subject', '')), '\n'.join(body[kind]), kind,
              str(msg.get('In-Reply-To', '')))
    return fields, files, issues

def import_emails(input_dir, output_dir):
    root, out = Path(input_dir).resolve(), Path(output_dir).resolve()
    for folder in ('inbox', 'sent'):
        if not (root / 'emails' / folder).is_dir():
            raise ValueError('MissingInputFolder: ' + folder)
    out.mkdir(parents=True, exist_ok=True)
    blobs = out / 'attachments'
    blobs.mkdir(exist_ok=True)
    db = sqlite3.connect(out / 'archive.sqlite3')
    db.executescript(SCHEMA)
    errors, warnings = [], []
    scanned = imported = skipped = 0
    try:
        for folder in ('inbox', 'sent'):
            for path in sorted((root / 'emails' / folder).rglob('*')):
                if not path.is_file() or path.suffix.lower() != '.eml':
                    continue
                scanned += 1
                source = str(path.resolve())
                try:
                    if not path.resolve().is_relative_to(root):
                        raise ValueError('SourceOutsideInput')
                    raw = path.read_bytes()
                    sha = digest(raw)
                    eid = digest(source.encode('utf-8'))
                    existing = db.execute('SELECT source_sha256 FROM emails WHERE source_path=?', (source,)).fetchone()
                    if existing:
                        if existing[0] != sha:
                            raise ValueError('SourceChanged')
                        skipped += 1
                        continue
                    fields, files, issues = parse(raw)
                    with db:
                        original = out / 'originals' / (eid + '.eml')
                        original.parent.mkdir(exist_ok=True)
                        store(original, raw)
                        db.execute('INSERT INTO emails VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                   (eid, source, folder, sha, *fields, str(original)))
                        for index, (part, name, mime, data) in enumerate(files):
                            h = digest(data)
                            target = blobs / h
                            store(target, data)
                            db.execute('INSERT OR IGNORE INTO attachments VALUES (?,?,?)', (h, len(data), str(target)))
                            db.execute('INSERT INTO email_attachments VALUES (?,?,?,?,?,?)',
                                       (digest((eid + ':' + part).encode()), eid, h, index, name, mime))
                    imported += 1
                    warnings.extend({'source_path': source, 'type': issue} for issue in issues)
                except Exception as exc:
                    errors.append({'source_path': source, 'type': type(exc).__name__,
                                   'reason': str(exc) if isinstance(exc, ValueError) else 'Import failed; inspect file and output permissions'})
        queries = {
            'emails.csv': 'SELECT * FROM emails ORDER BY source_path',
            'email_attachments.csv': '''SELECT ea.id, ea.email_id, e.source_path, e.source_folder,
                ea.part_index, ea.original_filename, ea.mime_type, a.size_bytes,
                a.sha256, a.stored_path FROM email_attachments ea
                JOIN emails e ON e.id=ea.email_id JOIN attachments a ON a.sha256=ea.attachment_sha256
                ORDER BY e.source_path, ea.part_index'''}
        for filename, query in queries.items():
            cursor = db.execute(query)
            with (out / filename).open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([c[0] for c in cursor.description])
                writer.writerows(cursor)
        count = lambda q: db.execute(q).fetchone()[0]
        summary = dict(total_emails=count('SELECT count(*) FROM emails'),
                       inbox=count("SELECT count(*) FROM emails WHERE source_folder='inbox'"),
                       sent=count("SELECT count(*) FROM emails WHERE source_folder='sent'"),
                       attachment_occurrences=count('SELECT count(*) FROM email_attachments'),
                       unique_attachment_hashes=count('SELECT count(*) FROM attachments'),
                       scanned=scanned, imported=imported, skipped=skipped, failures=len(errors),
                       error_types=dict(Counter(e['reason'] for e in errors)), errors=errors,
                       warning_types=dict(Counter(w['type'] for w in warnings)), warnings=warnings)
        (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / 'errors.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in errors + warnings), encoding='utf-8')
        return summary
    finally:
        db.close()

def store(path, data):
    # Never trust MIME filenames as disk paths. Existing blobs must match.
    if path.is_symlink():
        raise ValueError('UnsafeArchiveSymlink')
    try:
        with path.open('xb') as f:
            f.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError('ArchiveIntegrityMismatch')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = import_emails(args.input, args.output)
    print(json.dumps(result, ensure_ascii=True))
    return 1 if result['failures'] else 0

if __name__ == '__main__':
    raise SystemExit(main())
