import logging
import simplejson as json
import decimal
import re
from decimal import Decimal
from django.db import connection
from django.shortcuts import render
from django.http import HttpResponse
from django.http import HttpResponseNotFound
from django.http import HttpResponseServerError
from django.core.context_processors import csrf
from django.core.serializers.json import DjangoJSONEncoder

SRID = 4326  # this needs to match the SRID on the location field in psql.
DEFAULT_LIMIT = 200
TWO_PLACES = Decimal('0.01')
ACCESSION_TAB = 'lis_germplasm.grin_accession'
ACC_SELECT_COLS = ('gid', 'taxon', 'latdec', 'longdec', 'accenumb', 'elevation',
               'cropname', 'collsite', 'acqdate', 'origcty')


ORDER_BY_FRAG = '''
 ORDER BY ST_Distance(
  geographic_coord::geometry,
  ST_Centroid(
   ST_MakeEnvelope(%(minx)s, %(miny)s, %(maxx)s, %(maxy)s, %(srid)s)
  )
 ) ASC, taxon, gid
'''
LIMIT_FRAG = 'LIMIT %(limit)s'
COUNTRY_REGEX = re.compile(r'[a-z]{3,3}', re.I)

logger = logging.getLogger(__name__)


def _include_geo_bounds(p):
    if p.get('q', None):
        if p.get('limit_geo_bounds', None) == 'true':
            return True
        else:
            return False
    if p.get('country', None):
        return False
    return True

GRIN_ACC_WHERE_FRAGS = {
    'fts' : {
        'include' : lambda p: '|' in p.get('q', '') or '&' in p.get('q', ''),
        'sql' : "taxon_fts @@ to_tsquery('english', %(q)s)",
    },
    'fts_simple' : {
        'include' : lambda p: p.get('q', None) and not GRIN_ACC_WHERE_FRAGS['fts']['include'](p),
        'sql' : "taxon_fts @@ plainto_tsquery('english', %(q)s)",
    },
    'country' : {
        'include' : lambda p: p.get('country', None),
        'sql' : 'origcty = %(country)s',
    },
    'accession_ids' : {
        'include' : lambda p: p.get('accession_ids', None),
        'sql' : 'accenumb = ANY( %(accession_ids)s )',
    },
    'limit_geo_bounds' : {
        'include' : lambda p: p.get('limit_geo_bounds', None) == 'true',
        'sql' : '''
           latdec <> 0 AND longdec <> 0 AND
           ST_Contains(
            ST_MakeEnvelope(%(minx)s, %(miny)s, %(maxx)s, %(maxy)s, %(srid)s),
            geographic_coord::geometry
           )''',
    },
}

GRIN_EVAL_WHERE_FRAGS = {
    'accession prefix' : {
        'include' : lambda p: p.get('prefix', None),
        'sql' : 'accession_prefix = %(prefix)s',
    },
    'accession number' : {
        'include' : lambda p: p.get('acc_num', None),
        'sql' : 'accession_number = %(acc_num)s',
    },
    'accession surfix' : {
        'include' : lambda p: p.get('suffix', None),
        'sql' : 'accession_surfix = %(suffix)s',
    },
}


def index(req):
    '''Render the index template, which will boot up angular-js.
    '''
    return render(req, 'grin_app/index.html')


def evaluation_descr_names(req):
    '''Return JSON for all distinct trait descriptor names'''
    assert req.method == 'GET', 'GET request method required'
    sql = '''
    SELECT DISTINCT descriptor_name
    FROM lis_germplasm.legumes_grin_evaluation_data
    ORDER BY descriptor_name
    '''
    cursor = connection.cursor()
    cursor.execute(sql)
    names = [row[0] for row in cursor.fetchall()]
    result = json.dumps(names)
    response = HttpResponse(result, content_type='application/json')
    return response


def evaluation_detail(req):
    '''Return JSON for all evalation/trait records matching this accession id
    '''
    assert req.method == 'GET', 'GET request method required'
    params = req.GET.dict()
    assert 'accenumb' in params, 'missing accenumb param'
    parts = params['accenumb'].split()
    prefix, acc_num, rest = parts[0], parts[1], parts[2:]  # suffix optional
    suffix = ' '.join(rest)
    cursor = connection.cursor()
    sql_params = {
        'prefix' : prefix,
        'acc_num' : acc_num,
        'suffix' : suffix,
    }
    where_clauses = []
    for key, val in GRIN_EVAL_WHERE_FRAGS.items():
        if val['include'](sql_params):
            where_clauses.append(val['sql'])
    where_sql = ' AND '.join(where_clauses)
    sql = '''
    SELECT accession_prefix,
           accession_number,
           accession_surfix,
           observation_value,
           descriptor_name,
           method_name,
           plant_name,
           taxon,
           origin,
           original_value,
           frequency,
           low,
           hign,
           mean,
           sdev,
           ssize,
           inventory_prefix,
           inventory_number,
           inventory_suffix,
           accession_comment
    FROM lis_germplasm.legumes_grin_evaluation_data
    WHERE %s
    ORDER BY descriptor_name
    ''' % where_sql
    # logger.info(cursor.mogrify(sql, sql_params))
    cursor.execute(sql, sql_params)
    rows = _dictfetchall(cursor)
    result = json.dumps(rows, use_decimal=True)
    response = HttpResponse(result, content_type='application/json')
    return response
    

def accession_detail(req):
    '''Return JSON for all columns for a accession id.'''
    assert req.method == 'GET', 'GET request method required'
    params = req.GET.dict()
    assert 'accenumb' in params, 'missing accenumb param'
    # fix me: name the columns dont select *!
    sql= '''
    SELECT * FROM lis_germplasm.grin_accession WHERE accenumb = %(accenumb)s
    '''
    cursor = connection.cursor()
    # logger.info(cursor.mogrify(sql, params))
    cursor.execute(sql, params)
    rows = _dictfetchall(cursor)
    return _acc_search_response(rows)


def countries(req):
    '''Return a json array of countries for search filtering ui.'''
    cursor = connection.cursor()
    sql = '''
    SELECT DISTINCT origcty FROM lis_germplasm.grin_accession ORDER by origcty
    '''
    cursor.execute(sql)
    # flatten into array, and filter out bogus records like '' or
    # 3 number codes.
    countries = [row[0] for row in cursor.fetchall()
                 if row[0] and COUNTRY_REGEX.match(row[0])]
    result = json.dumps(countries)
    response = HttpResponse(result, content_type='application/json')
    return response


def search(req):
    '''Search by map bounds and return GeoJSON results.'''
    assert req.method == 'GET', 'GET request method required'
    params = req.GET.dict()
    if 'limit' not in params:
        params['limit'] = DEFAULT_LIMIT
    where_clauses = []
    for key, val in GRIN_ACC_WHERE_FRAGS.items():
        if val['include'](params):
            where_clauses.append(val['sql'])
    if len(where_clauses) == 0:
        where_sql = ''
    else:
        where_sql = 'WHERE %s' % ' AND '.join(where_clauses)
    cols_sql = ' , '.join(ACC_SELECT_COLS)
    
    params['limit'] = int(params['limit'])
    if int(params['limit']) == 0:
        params['limit'] = 'ALL'  # LIMIT ALL -- no limit
        
    sql = '''SELECT %s FROM %s %s %s %s''' % (
        cols_sql,
        ACCESSION_TAB,
        where_sql,
        ORDER_BY_FRAG,
        LIMIT_FRAG,
    )
    cursor = connection.cursor()
    sql_params = {
        'q' : params.get('q', None),
        'country' : params.get('country', None),
        'minx' : float(params.get('sw_lng', 0)),
        'miny' : float(params.get('sw_lat', 0)),
        'maxx' : float(params.get('ne_lng', 0)),
        'maxy' : float(params.get('ne_lat', 0)),
        'limit': int(params['limit']),
        'srid' : SRID,
    }
    if(params.get('accession_ids', None)):
        if ',' in params['accession_ids']:
            sql_params['accession_ids'] = params['accession_ids'].split(',')
        else:
            sql_params['accession_ids'] = [params['accession_ids']];
    # logger.info(cursor.mogrify(sql, sql_params))
    cursor.execute(sql, sql_params)
    rows = _dictfetchall(cursor)
    return _acc_search_response(rows)
   

def _acc_search_response(rows):
    geo_json = []
    # logger.info('results: %d' % len(rows))
    for rec in rows:
        # fix up properties which are not json serializable
        if rec.get('acqdate', None):
            rec['acqdate'] = str(rec['acqdate'])
        else:
            rec['acqdate'] = None
        if rec.get('colldate', None):
            rec['colldate'] = str(rec['colldate'])
        else:
            rec['colldate'] = None
        # geojson can have null coords, so output this for
        # non-geocoded search results (e.g. full text search w/ limit
        # to current map extent turned off
        if rec.get('longdec', 0) == 0 and rec.get('latdec', 0) == 0:
            coords = None
        else:
            lat = Decimal(rec['latdec']).quantize(TWO_PLACES)
            lng = Decimal(rec['longdec']).quantize(TWO_PLACES)
            coords = [lng, lat]
            del rec['latdec']  # have been translated into geojson coords, 
            del rec['longdec'] # so these keys are extraneous now.
        geo_json_frag = {
            'type' : 'Feature',
            'geometry' : {
                'type' : 'Point',
                'coordinates' : coords
            },
            'properties' : rec  # rec happens to be a dict of properties. yay
        }
        geo_json.append(geo_json_frag)
    result = json.dumps(geo_json, use_decimal=True)
    response = HttpResponse(result, content_type='application/json')
    return response


def _dictfetchall(cursor):
    "Return all rows from a cursor as a dict"
    columns = [col[0] for col in cursor.description]
    return [
        dict(zip(columns, row))
        for row in cursor.fetchall()
    ]
