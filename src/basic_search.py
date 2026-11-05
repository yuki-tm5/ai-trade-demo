"""T05 offline metadata search. Archive is always read-only."""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from offline_mail import digest

FIELDS = ('subject', 'body', 'filename')
TOKEN = re.compile(r'(?<![a-z0-9])[a-z0-9]+(?:-[a-z0-9]+)*(?![a-z0-9])', re.I)

def tokens(text):
    return {m.group().casefold() for m in TOKEN.finditer(text)
            if re.search('[a-z]', m.group(), re.I) and re.search('[0-9]', m.group())}

def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)

def build_index(archive, index):
    archive, index = Path(archive).resolve(), Path(index).resolve()
    if index.exists():
        raise FileExistsError('Index exists; use a new index path')
    if not archive.is_file():
        raise FileNotFoundError('Archive database missing')
    index.parent.mkdir(parents=True, exist_ok=True)
    initial_hash = digest(archive.read_bytes())
    with closing(readonly(archive)) as source:
        source.row_factory = sqlite3.Row
        emails = [dict(r) for r in source.execute('SELECT * FROM emails ORDER BY id')]
        relations = [dict(r) for r in source.execute('''SELECT ea.*, a.stored_path, a.size_bytes
            FROM email_attachments ea JOIN attachments a ON a.sha256=ea.attachment_sha256 ORDER BY ea.id''')]
    by_email = {}
    for r in relations:
        by_email.setdefault(r['email_id'], []).append(r)
    with closing(sqlite3.connect(index)) as db:
        db.executescript('''CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE documents (id TEXT PRIMARY KEY, subject TEXT, body TEXT, filename TEXT, payload TEXT);
            CREATE TABLE model_tokens (document_id TEXT, field TEXT, token TEXT,
                PRIMARY KEY(document_id,field,token));
            CREATE INDEX token_lookup ON model_tokens(token,field,document_id);''')
        for email in emails:
            for relation in [None] + by_email.get(email['id'], []):
                did = 'attachment:' + relation['id'] if relation else 'email:' + email['id']
                payload = dict(result_type='attachment' if relation else 'email', email_id=email['id'],
                    attachment_relation_id=relation['id'] if relation else None,
                    attachment_sha256=relation['attachment_sha256'] if relation else None,
                    filename=relation['original_filename'] if relation else Path(email['original_path']).name,
                    subject=email['subject'], date=email['date_raw'], date_utc=email['date_utc'],
                    sender=email['sender'], source_folder=email['source_folder'], source_path=email['source_path'],
                    archived_path=str(archive.parent/'attachments'/relation['attachment_sha256']) if relation else str(archive.parent/'originals'/Path(email['original_path']).name),
                    archived_email_path=str(archive.parent/'originals'/Path(email['original_path']).name),
                    sha256=relation['attachment_sha256'] if relation else email['source_sha256'])
                values = (email['subject'] or '', email['body'] or '', relation['original_filename'] or '' if relation else '')
                db.execute('INSERT INTO documents VALUES (?,?,?,?,?)',
                    (did,*(v.casefold() for v in values),json.dumps(payload,ensure_ascii=False)))
                for field,value in zip(FIELDS,values):
                    db.executemany('INSERT INTO model_tokens VALUES (?,?,?)',
                        [(did,field,t) for t in sorted(tokens(value))])
        db.executemany('INSERT INTO metadata VALUES (?,?)',
            [('archive_path',str(archive)),('archive_sha256',initial_hash),('engine','parameterized-instr-and-exact-model-token')])
        db.commit()
    if digest(archive.read_bytes()) != initial_hash:
        raise ValueError('Archive changed during indexing')
    return {'emails_indexed':len(emails),'attachment_relations_indexed':len(relations),
            'documents_indexed':len(emails)+len(relations),'index_path':str(index),'archive_sha256':initial_hash}

def search(index, query, field='all', mode='auto'):
    query = query.strip()
    if not query:
        raise ValueError('Search query must not be empty')
    if field not in ('all',*FIELDS) or mode not in ('auto','keyword','model'):
        raise ValueError('Invalid field or mode')
    q = query.casefold()
    model = mode == 'model' or (mode == 'auto' and q in tokens(q))
    if model and q not in tokens(q):
        raise ValueError('Model mode requires one complete alphanumeric identifier')
    fields = FIELDS if field == 'all' else (field,)
    with closing(readonly(index)) as db:
        db.row_factory = sqlite3.Row
        meta = dict(db.execute('SELECT key,value FROM metadata').fetchall())
        if digest(Path(meta['archive_path']).read_bytes()) != meta['archive_sha256']:
            raise ValueError('Archive changed; build a fresh index before searching')
        if model:
            slots = ','.join('?' for _ in fields)
            rows = db.execute('''SELECT * FROM documents WHERE id IN
                (SELECT document_id FROM model_tokens WHERE token=? AND field IN (''' + slots + ')) ORDER BY id', (q,*fields)).fetchall()
        else:
            # SQL identifiers come exclusively from FIELDS; all user values are bound parameters.
            where = ' OR '.join('instr('+f+',?)>0' for f in fields)
            rows = db.execute('SELECT * FROM documents WHERE '+where+' ORDER BY id', [q]*len(fields)).fetchall()
        results = []
        for row in rows:
            result = json.loads(row['payload'])
            bases = [f for f in fields if (q in tokens(row[f]) if model else q in row[f])]
            result['match_basis'] = [{'field':f,'method':'exact_model_token' if model else 'literal_substring',
                                     'query':query,'inherited_from_email':result['result_type']=='attachment' and f != 'filename'} for f in bases]
            p = Path(result['archived_path'])
            result['file_exists'] = p.is_file()
            result['sha256_verified'] = p.is_file() and digest(p.read_bytes()) == result['sha256']
            results.append(result)
    return {'query':query,'field':field,'effective_mode':'model' if model else 'keyword',
            'status':'matched' if results else 'no_match','result_count':len(results),
            'email_result_count':sum(r['result_type']=='email' for r in results),
            'attachment_result_count':sum(r['result_type']=='attachment' for r in results),
            'results':results}

def attachment_path(index, relation_id):
    with closing(readonly(index)) as db:
        row=db.execute('SELECT payload FROM documents WHERE id=?',('attachment:'+relation_id,)).fetchone()
        if not row:
            raise ValueError('No matching attachment relation')
        payload=json.loads(row[0])
        p=Path(payload['archived_path'])
        if not p.is_file() or digest(p.read_bytes()) != payload['sha256']:
            raise ValueError('Archived attachment missing or hash mismatch')
        return p

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    build=sub.add_parser('index')
    build.add_argument('--archive',required=True)
    build.add_argument('--index',required=True)
    find=sub.add_parser('search')
    find.add_argument('--index',required=True)
    find.add_argument('--query',required=True)
    find.add_argument('--field',choices=('all',*FIELDS),default='all')
    find.add_argument('--mode',choices=('auto','keyword','model'),default='auto')
    op=sub.add_parser('open')
    op.add_argument('--index',required=True)
    op.add_argument('--attachment-id',required=True)
    op.add_argument('--launch',action='store_true',help='Explicitly launch local default application; otherwise print verified path')
    args=parser.parse_args()
    try:
        if args.action=='index':
            result=build_index(args.archive,args.index)
        elif args.action=='search':
            result=search(args.index,args.query,args.field,args.mode)
        else:
            p=attachment_path(args.index,args.attachment_id)
            if args.launch:
                os.startfile(str(p))
            result={'archived_path':str(p),'sha256_verified':True,'launched':args.launch}
        print(json.dumps(result,ensure_ascii=True,indent=2))
        return 0
    except (OSError,ValueError,sqlite3.Error) as exc:
        print(json.dumps({'status':'error','error_type':type(exc).__name__,'message':str(exc)},ensure_ascii=True),file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
