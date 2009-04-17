#!/usr/bin/python2.5
from time import time, sleep
import catalog.marc.fast_parse as fast_parse
import web, sys, codecs, re
import catalog.importer.pool as pool
from catalog.utils.query import query_iter
from catalog.importer.merge import try_merge
from catalog.marc.new_parser import read_edition
from catalog.importer.load import build_query
from catalog.importer.lang import add_lang
from catalog.get_ia import files, read_marc_file
from catalog.merge.merge_marc import build_marc
from catalog.importer.db_read import get_mc, withKey
sys.path.append('/home/edward/src/olapi')
from olapi import OpenLibrary

from catalog.read_rc import read_rc

rc = read_rc()

marc_index = web.database(dbn='postgres', db='marc_index')
marc_index.printing = False

db_amazon = web.database(dbn='postgres', db='amazon')
db_amazon.printing = False

ol = OpenLibrary("http://openlibrary.org")
ol.login('ImportBot', rc['ImportBot']) 

sys.stdout = codecs.getwriter('utf-8')(sys.stdout)

t0 = time()
t_prev = time()
rec_no = 0
chunk = 50
load_count = 0

archive_url = "http://archive.org/download/"

archive_id = sys.argv[1]

def percent(a, b):
    return float(a * 100.0) / b

def progress(archive_id, rec_no, loc, start_pos, pos):
    global t_prev, load_count
    cur_time = time()
    t = cur_time - t_prev
    t_prev = cur_time
    t1 = cur_time - t0
    rec_per_sec = chunk / t
    bytes_per_sec_total = (pos - start_pos) / t1

    q = {
        'cur': loc,
        'chunk': chunk,
        'rec_no': rec_no,
        't': t,
        't1': t1,
        'part': part,
        'pos': pos,
        'load_count': load_count,
        'time': cur_time,
        'bytes_per_sec_total': bytes_per_sec_total,
    }
    pool.post_progress(archive_id, q)

def is_loaded(loc):
    db_iter = marc_index.query('select * from machine_comment where v=$loc', {'loc': loc})
    if list(db_iter):
        return True
    iter = query_iter({'type': '/type/edition', 'source_records': 'marc:' + loc})
    return bool(list(iter))

re_meta_mrc = re.compile('^([^/]*)_meta.mrc:0:\d+$')

def amazon_source_records(asin):
    iter = db_amazon.select('amazon', where='asin = $asin', vars={'asin':asin})
    return ["amazon:%s:%s:%d:%d" % (asin, r.seg, r.start, r.length) for r in iter]

def fix_toc(e):
    toc = e.get('table_of_contents', None)
    if not toc:
        return
    if isinstance(toc[0], dict) and toc[0]['type'] == '/type/toc_item':
        return
    if isinstance(toc[0], basestring):
        assert all(isinstance(i, basestring) for i in toc)
        return [{'title': i, 'type': '/type/toc_item'} for i in toc]
    else:
        assert all(i['type'] == '/type/text' for i in toc)
        return [{'title': i['value'], 'type': '/type/toc_item'} for i in toc]

re_skip = re.compile('\b([A-Z]|Co|Dr|Jr|Capt|Mr|Mrs|Ms|Prof|Rev|Revd|Hon)\.$')

def has_dot(s):
    return s.endswith('.') and not re_skip.search(s)

def add_source_records(key, new, e):
    sr = None
    if 'source_records' in e:
        sr = e['source_records']
    else:
        existing = get_mc(key)
        amazon = 'amazon:'
        if existing.startswith(amazon):
            print 'amazon:', existing
            sr = amazon_source_records(existing[len(amazon):]) or [existing]
        else:
            m = re_meta_mrc.match(existing)
            sr = ['marc:' + existing if not m else 'ia:' + m.group(1)]
    sr += ['marc:' + new]
    q = {
        'key': key,
        'source_records': { 'connect': 'update_list', 'value': sr }
    }

    # fix other bits of the record as well
    new_toc = fix_toc(e)
    if new_toc:
        q['table_of_contents'] = {'connect': 'update_list', 'value': new_toc }
    if e.get('subjects', None) and any(has_dot(s) for s in e['subjects']):
        subjects = [s[:-1] if has_dot(s) else s for s in e['subjects']]
        q['subjects'] = {'connect': 'update_list', 'value': subjects }
    print ol.write(q, 'found a matching MARC record')

def load_part(archive_id, part, start_pos=0):
    global rec_no, t_prev, load_count
    full_part = archive_id + "/" + part
    f = open(rc['marc_path'] + full_part)
    if start_pos:
        f.seek(start_pos)
    for pos, loc, data in read_marc_file(full_part, f, pos=start_pos):
        rec_no += 1
        if rec_no % chunk == 0:
            progress(archive_id, rec_no, loc, start_pos, pos)

        if is_loaded(loc):
            continue
        try:
            index_fields = fast_parse.index_fields(data, ['010', '020', '035', '245'])
        except KeyError:
            print loc
            print fast_parse.get_tag_lines(data, ['245'])
            raise
        except AssertionError:
            print loc
            raise
        if not index_fields or 'title' not in index_fields:
            continue

        edition_pool = pool.build(index_fields)

        if not edition_pool:
            yield loc, data
            continue

        rec = fast_parse.read_edition(data)
        e1 = build_marc(rec)

        match = False
        for k, v in edition_pool.iteritems():
            for edition_key in v:
                thing = withKey(edition_key)
                assert thing
                if try_merge(e1, edition_key, thing):
                    add_source_records(edition_key, loc, thing)
                    match = True
                    break
            if match:
                break

        if not match:
            yield loc, data

start = pool.get_start(archive_id)
go = 'part' not in start

print archive_id

def write(q):
    if 0:
        for i in range(10):
            try:
                return ol.new(q, comment='initial import')
            except (KeyboardInterrupt, NameError):
                raise
            except:
                pass
            sleep(30)
    try:
        return ol.new(q, comment='initial import')
    except:
        print q
        raise

def write_edition(loc, edition):
    add_lang(edition)
    q = build_query(loc, edition)
    q['source_records'] = ['marc:' + loc]

    ret = write(q)
    print ret
    assert ret['status'] == 'ok'
    assert 'created' in ret
    editions = [i for i in ret['created'] if i.startswith('/b/OL')]
    assert len(editions) == 1
    key = editions[0]
    # get key from return
    pool.update(key, q)

for part, size in files(archive_id):
#for part, size in marc_loc_updates:
    print part, size
    if not go:
        if part == start['part']:
            go = True
            print "starting %s at %d" % (part, start['pos'])
            part_iter = load_part(archive_id, part, start_pos=start['pos'])
        else:
            continue
    else:
        part_iter = load_part(archive_id, part)

    for loc, data in part_iter:
        #if loc == 'marc_binghamton_univ/bgm_openlib_final_10-15.mrc:265680068:4538':
        #    continue
        try:
            edition = read_edition(loc, data)
        except AssertionError:
            print loc
            raise
        if edition['title'] == 'See.':
            print 'See.', edition
            continue
        if edition['title'] == 'See also.':
            print 'See also.', edition
            continue
        load_count += 1
        if load_count % 100 == 0:
            print "load count", load_count
        write_edition(loc, edition)

print "finished"
