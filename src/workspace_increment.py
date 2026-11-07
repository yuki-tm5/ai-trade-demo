"""Rebuild the isolated, stage-one Demo snapshot from bundled evidence."""
from contextlib import closing
import json
import shutil
import sqlite3

from ask_find import ask, build_catalog
from basic_search import readonly
from document_content import archive_sources, build
from offline_mail import digest, import_emails
from workspace_ops import parse_events


def build_snapshot(root, target):
    fixture = root / 'workspace_increment'
    events = parse_events(fixture, 1)
    if len(events) != 1:
        raise ValueError('Invalid incremental Demo evidence')
    inbox = target / 'input/emails/inbox'
    inbox.mkdir(parents=True)
    (inbox.parent / 'sent').mkdir()
    shutil.copyfile(fixture / events[0]['source_file'], inbox / events[0]['source_file'])
    archive = target / 'archive'
    shutil.copytree(root / 'data/archive', archive)
    archive_db = archive / 'archive.sqlite3'
    with closing(sqlite3.connect(archive_db)) as db, db:
        db.execute("UPDATE attachments SET stored_path=? || sha256", (str(archive / 'attachments') + '/',))
    result = import_emails(target / 'input', archive, relative_identity=True)
    if result['failures'] or result['imported'] != 1:
        raise ValueError('Demo mail import failed')
    repeat = import_emails(target / 'input', archive, relative_identity=True)
    if repeat['failures'] or repeat['imported'] or repeat['skipped'] != 1:
        raise ValueError('Demo mail deduplication failed')

    sources = archive_sources(archive_db)
    build(sources, target / 'content.sqlite3')
    config_path = target / 'config.json'
    config_path.write_text(json.dumps({
        'catalog': 'catalog.sqlite3',
        'datasets': {'supplementary': {'archive_root': 'archive', 'content_index': 'content.sqlite3'}},
    }), encoding='utf-8')
    build_catalog(config_path)
    for name in (archive_db, target / 'content.sqlite3', target / 'catalog.sqlite3'):
        with closing(sqlite3.connect(name)) as db:
            if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Demo index integrity failure')
    def document_keys(path):
        with closing(readonly(path)) as db:
            return set(db.execute('SELECT dataset,sha256 FROM documents'))

    if not document_keys(root / 'data/catalog.sqlite3') <= document_keys(target / 'catalog.sqlite3'):
        raise ValueError('Existing Demo documents missing')
    match = ask(config_path, {'intent': 'factory_quotes', 'product_id': events[0]['project'], 'dataset': 'supplementary'})
    email_id = digest(('emails/inbox/' + events[0]['source_file']).encode('utf-8'))
    if not any(source['email_id'] == email_id for document in match['documents']
               for source in document['sources']):
        raise ValueError('New Demo mail is not searchable')
    return config_path, events
