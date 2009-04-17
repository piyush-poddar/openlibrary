from catalog.merge.merge_marc import *
from catalog.read_rc import read_rc
import catalog.merge.amazon as amazon
from catalog.get_ia import *
from catalog.importer.db_read import withKey, get_mc
import catalog.marc.fast_parse as fast_parse
import xml.parsers.expat
import web

rc = read_rc()

ia_db = web.database(dbn='mysql', db='archive', user=rc['ia_db_user'], pw=rc['ia_db_pass'], host=rc['ia_db_host'])
ia_db.printing = False

threshold = 875
index_path = '/1/pharos/edward/index/2/'
amazon.set_isbn_match(225)

def try_amazon(thing):
    if 'isbn_10' not in thing:
        return None
    if 'authors' in thing:
        authors = []
        for a in thing['authors']:
            author_thing = withKey(a['key'])
            if 'name' in author_thing:
                authors.append(author_thing['name'])
    else:
        authors = []
    return amazon.build_amazon(thing, authors)

def is_dark(ia):
    iter = ia_db.query('select curatestate from metadata where identifier=$ia', { 'ia': ia })
    rows = list(iter)
    assert len(rows) == 1
    return rows[0].curatestate == 'dark'

def marc_match(e1, loc):
    rec = fast_parse.read_edition(get_from_local(loc))
    try:
        e2 = build_marc(rec)
    except TypeError:
        print rec
        raise
    return attempt_merge(e1, e2, threshold, debug=False)

def ia_match(e1, ia):
    loc, rec = get_ia(ia)
    try:
        e2 = build_marc(rec)
    except TypeError:
        print rec
        raise
    return attempt_merge(e1, e2, threshold, debug=False)

def amazon_match(e1, thing):
    try:
        a = try_amazon(thing)
    except IndexError:
        print thing['key']
        raise
    except AttributeError:
        return False
    if not a:
        return False
    try:
        return amazon.attempt_merge(a, e1, threshold, debug=False)
    except:
        print a
        print e1
        print thing['key']
        raise

def source_records_match(e1, thing):
    marc = 'marc:'
    amazon = 'amazon:'
    ia = 'ia:'
    match = False
    for src in thing['source_records']:
        if src.startswith(marc):
            if marc_match(e1, src[len(marc):]):
                match = True
                break
        elif src.startswith(ia):
            if ia_match(e1, src[len(ia):]):
                match = True
                break
        else:
            assert src.startswith(amazon)
            if amazon_match(e1, thing):
                match = True
                break
    return match

def try_merge(e1, edition_key, thing):
    thing_type = thing['type']['key']
    if thing_type == '/type/delete': # 
        return False
    assert thing_type == '/type/edition'

    if 'source_records' in thing:
        return source_records_match(e1, thing)

    ia = thing.get('ocaid', None)
    print edition_key
    mc = get_mc(edition_key)
    print mc
    if mc:
        if mc.startswith('ia:'):
            ia = mc[3:]
        elif mc.endswith('.xml') or mc.endswith('.mrc'):
            ia = mc[:mc.find('/')]
        if '_meta.mrc:' in mc:
            assert 'ocaid' in thing
            ia = thing['ocaid']
    rec2 = None
    if ia:
        if is_dark(ia):
            return False
        try:
            loc2, rec2 = get_ia(ia)
        except xml.parsers.expat.ExpatError:
            return False
        except urllib2.HTTPError, error:
            print error.code
            assert error.code in (404, 403)
        if not rec2:
            return True
    if not rec2:
        if not mc:
            mc = get_mc(thing['key'])
        if not mc:
            return False
        if mc.startswith('amazon:'):
            try:
                a = try_amazon(thing)
            except IndexError:
                print thing['key']
                raise
            except AttributeError:
                return False
            if not a:
                return False
            try:
                return amazon.attempt_merge(a, e1, threshold, debug=False)
            except:
                print a
                print e1
                print thing['key']
                raise
        try:
            data = get_from_local(mc)
            if not data:
                return True
            rec2 = fast_parse.read_edition(data)
        except (fast_parse.SoundRecording, IndexError, AssertionError):
            print mc
            print edition_key
            return False
        except:
            print mc
            print edition_key
            raise
    if not rec2:
        return False
    try:
        e2 = build_marc(rec2)
    except TypeError:
        print rec2
        raise
    return attempt_merge(e1, e2, threshold, debug=False)
