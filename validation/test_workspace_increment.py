"""Regression checks against frozen fictional identities, dates and workbook cells."""
from email import policy
from email.parser import BytesParser
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ask_find import ask, config, resolve_file
from workspace_increment import build_snapshot
from workspace_ops import change, ingest, initial, parse_events

QUERY = dict(intent='factory_quotes', product_id='ZZ81001', dataset='supplementary')
MESSAGE_ID = '<quote-increment@workspace.invalid>'
DATES = {'2026-11-10', '2026-11-14'}
BUCKETS = ('documents', 'uncertain_documents', 'audit_documents')


def results(path):
    return ask(path, QUERY)


def attachments(result):
    return {doc['attachment_id'] for bucket in BUCKETS for doc in result[bucket]}


baseline = results(ROOT / 'config.json')
with TemporaryDirectory() as first, TemporaryDirectory() as second:
    path, events = build_snapshot(ROOT, Path(first))
    restarted, _ = build_snapshot(ROOT, Path(second))
    after = results(path)
    assert attachments(baseline) < attachments(after)
    assert attachments(after) == attachments(results(restarted))
    new_id, = attachments(after) - attachments(baseline)
    document, = [d for d in after['documents'] if d['attachment_id'] == new_id]
    source, = document['sources']
    original = path.parent / 'archive/originals' / Path(source['archived_email_path']).name
    msg = BytesParser(policy=policy.default).parsebytes(original.read_bytes())
    assert msg['Message-ID'] == MESSAGE_ID
    assert original.read_bytes() == (ROOT / 'workspace_increment/quote_increment.eml').read_bytes()
    assert resolve_file(config(path), document['file_reference']).read_bytes() == next(msg.iter_attachments()).get_payload(decode=True)

    state = ingest(initial(), parse_events(ROOT / 'workspace_fixtures', 0))
    task_id = next(k for k, t in state['tasks'].items() if t['project'] == 'ZZ81002')
    state = change(state, 'done', task_id=task_id)
    completed = state['tasks'][task_id]['completed_at']
    all_events = parse_events(ROOT / 'workspace_fixtures', 1) + events
    updated = ingest(state, all_events)
    assert ingest(updated, all_events) == updated
    assert len(updated['tasks']) == len(state['tasks']) == 3
    task = updated['tasks'][task_id]
    assert task['status'] == 'done' and task['completed_at'] == completed
    assert task['confirmation_status'] == 'conflicting_evidence'
    assert {d for eid in task['sources'] for d in updated['events'][eid]['recorded_dates']} == DATES
    assert sum(e['source_email_id'] == MESSAGE_ID for e in updated['events'].values()) == 1
print('PASS: existing and new search results, restart rebuild, original MIME bytes, idempotent events, completed task, conflict evidence')
