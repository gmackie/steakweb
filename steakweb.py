#!/usr/bin/env python3

from aiohttp import web
import aiohttp
import aiohttp_jinja2
import aiohttp_session
from aiohttp_session.cookie_storage import EncryptedCookieStorage
import asyncio
import asyncpg
import jinja2
import json
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.idp_metadata_parser import OneLogin_Saml2_IdPMetadataParser
import os
import secrets
import socket
import stat
import string
import time

dbconn = None

with open('config.json', 'r') as configfile:
    config = json.load(configfile)

SAML_SETTINGS = config['saml_settings']
saml_req_data = config['saml_req_data']
socketpath = config['socket_path']
dbconnstr = config['dbconnstr']
cookiekey = config['cookiekey']
IDP_METADATA = config['idp_metadata']
# OMNIDAT: shared secret the shadydect/omniDECT node uses for the *77 provisioning
# API (parity with the edge worker's API_TOKEN). Human auth stays SAML; the node
# is a trusted machine caller and presents this token instead.
api_token = config.get('api_token', '')
# central Asterisk the DECT nodes register activated extensions to (SIP-FP);
# empty = don't return SIP creds from /api/activate (extension-only responses)
sip_registrar = config.get('sip_registrar', '')

### utility functions

def send_to_idp():
    raise web.HTTPFound(OneLogin_Saml2_Auth(saml_req_data, old_settings=SAML_SETTINGS).login())

def check_session_exp(session):
    if not 'iat' in session or session['iat'] + (60 * 20) < time.time():
        # session is expired, raise an exception to send through saml
        send_to_idp()

def check_auth_isadmin(session):
    # Check for "Extension Admins" group in Authentik
    attrs = session.get('attributes', None)
    if attrs:
        groups = attrs.get('http://schemas.xmlsoap.org/claims/Group', None)
        if groups:
            return 'Extension Admins' in groups
    return False

def gen_sip_pw():
    return secrets.token_urlsafe(8)
    
### get request handlers

async def homepage(request):
    # figure out who's calling and list the extensions they can control
    session = await aiohttp_session.get_session(request)
    check_session_exp(session)
    if dbconn is None:
        await init_db_pool()
    rows = None
    if check_auth_isadmin(session):
        rows = await dbconn.fetch("SELECT extn, name, switch, auth_code, publish FROM registered_extensions WHERE provisioned = 't' ORDER BY extn")
    else:
        rows = await dbconn.fetch("SELECT extn, name, switch, auth_code, publish FROM registered_extensions WHERE userid = $1 ORDER BY extn", session['uid'])

    # render the template with the list and the status of the last request (from the session)
    context = { 'extensions': rows, 'error': session.get('error', None), 'attributes': session.get('attributes', None) }
    r = aiohttp_jinja2.render_template('homepage.html', request, context)

    # clear the status
    session['error'] = None

    return r

async def directory(request):
    # public listing of published extensions (no auth)
    if dbconn is None:
        await init_db_pool()
    rows = await dbconn.fetch("SELECT extn, name FROM registered_extensions WHERE publish = 't' ORDER BY lower(name), extn")
    context = { 'extensions': rows }
    return aiohttp_jinja2.render_template('directory.html', request, context)


async def directory_json(request):
    # public JSON of published extensions (no auth); consumable by shady.tel
    if dbconn is None:
        await init_db_pool()
    rows = await dbconn.fetch("SELECT extn, name FROM registered_extensions WHERE publish = 't' ORDER BY lower(name), extn")
    listings = [{ 'name': r['name'], 'number': r['extn'] } for r in rows]
    return web.json_response(listings, headers={'Access-Control-Allow-Origin': '*'})

### post request handlers

async def rename_extn(request):
    # Just set the name
    data = await request.post()
    session = await aiohttp_session.get_session(request)
    check_session_exp(session)
    if dbconn is None:
        await init_db_pool()

    n = None
    if check_auth_isadmin(session):
        n = await dbconn.execute('UPDATE registered_extensions SET name = $1 WHERE extn = $2', data['name'], int(data['extn']))
    else:
        n = await dbconn.execute('UPDATE registered_extensions SET name = $1 WHERE extn = $2 AND userid = $3', data['name'], int(data['extn']),session['uid'])

    if n != 'UPDATE 1':
        session['error'] = 'Could not change directory name; contact support'
        print(f'While updating extension name: {n}')

    raise web.HTTPFound('/')

async def delete_extn(request):
    # delete it where it doesn't have a physical circuit
    # note, sip happens automatically
    data = await request.post()
    session = await aiohttp_session.get_session(request)
    check_session_exp(session)
    if dbconn is None:
        await init_db_pool()

    n = None
    if check_auth_isadmin(session):
        n = await dbconn.execute('DELETE FROM registered_extensions WHERE switch IS NULL AND extn = $1', int(data['extn']))
    else:
        n = await dbconn.execute('DELETE FROM registered_extensions WHERE switch IS NULL AND extn = $1 AND userid = $2', int(data['extn']), session['uid'])

    if n != 'DELETE 1':
        session['error'] = 'Could not unsubscribe service; contact support'
        print(f'While deleting extension: {n}')

    raise web.HTTPFound('/')

async def create_extn(request):
    # make a new extension with a random auth_code
    # validate the submitted extension up front so bad input (e.g. letters)
    # gives the customer an error instead of a 500
    data = await request.post()
    session = await aiohttp_session.get_session(request)
    check_session_exp(session)

    extn = data.get('extn', '').strip()
    if not (len(extn) == 4 and extn.isascii() and extn.isdigit()):
        session['error'] = 'Extension must be a four-digit number'
        raise web.HTTPFound('/')
    extnum = int(extn)
    if extnum < 2000 or extnum >= 7000:
        session['error'] = 'Extension number must start with 2, 3, 4, 5, or 6'
        raise web.HTTPFound('/')

    if dbconn is None:
        await init_db_pool()

    name = data.get('name', '')
    publish = bool(data.get('publish'))   # BOOLEAN column: bind a real bool, not 't'/'f'
    if data['type'] == 'sip':
        switch = 11
        authcode = gen_sip_pw()
    elif data['type'] == 'dect':
        # OMNIDAT / omniDECT: assign the DECT switch (20) now; the handset binds
        # later via *77 (prov_to_dect sets provisioned + ipui). The activation
        # code is what the camper enters at *77 to claim this extension.
        switch = DECT_SWITCH
        authcode = f'{secrets.randbelow(1000000000000):012d}'
    else:
        switch = None
        authcode = f'{secrets.randbelow(1000000000000):012d}'

    try:
        n = await dbconn.execute('INSERT INTO registered_extensions (extn, name, userid, auth_code, publish, switch) VALUES ($1, $2, $3, $4, $5, $6)', extnum, name, session['uid'], authcode, publish, switch)
    except asyncpg.UniqueViolationError:
        session['error'] = f'Extension {extnum} is already taken; please choose another'
        raise web.HTTPFound('/')
    if n != 'INSERT 0 1':
        session['error'] = 'Could not subscribe service; contact support'
        print(f'While creating extension: {n}')

    raise web.HTTPFound('/')

async def publish_extn(request):
    # just set the published flag
    data = await request.post()
    session = await aiohttp_session.get_session(request)
    check_session_exp(session)
    if dbconn is None:
        await init_db_pool()

    n = None
    if check_auth_isadmin(session):
        n = await dbconn.execute("UPDATE registered_extensions SET publish = $2 WHERE extn = $1", int(data['extn']), data.get('published', '0')== '1')
    else:
        n = await dbconn.execute("UPDATE registered_extensions SET publish = $2 WHERE extn = $1 AND userid = $3", int(data['extn']), data.get('publish', '1') == '1', session['uid'])

    if n != 'UPDATE 1':
        session['error'] = 'Could not change directory name; contact support'
        print(f'While publishing extension: {n}')

    raise web.HTTPFound('/')

async def prov_to_sip(request):
    # if it doesn't have a physical circuit:
    # choose a random password
    # set the SIP password, and the switch ID to 11
    # maybe in a retry loop, tell the activation server to resync that extension
    data = await request.post()
    session = await aiohttp_session.get_session(request)
    check_session_exp(session)
    if dbconn is None:
        await init_db_pool()

    n = None
    if check_auth_isadmin(session):
        n = await dbconn.execute("UPDATE registered_extensions SET auth_code = $2, switch = 11 WHERE extn = $1", int(data['extn']), gen_sip_pw())
    else:
        n = await dbconn.execute("UPDATE registered_extensions SET auth_code = $2, switch = 11 WHERE extn = $1 AND userid = $3", int(data['extn']), gen_sip_pw(), session['uid'])

    if n != 'UPDATE 1':
        session['error'] = 'Could not change directory name; contact support'
        print(f'While applying SIP: {n}')

    raise web.HTTPFound('/')

# OMNIDAT / omniDECT: provision an extension onto the DECT switch (20), binding
# it to a cordless handset by IPUI. Parallel to prov_to_sip; the shadydect FP
# (dect/fp/registry.py) is the endpoint. See github.com/<you>/shadydect.
DECT_SWITCH = 20

async def prov_to_dect(request):
    data = await request.post()
    session = await aiohttp_session.get_session(request)
    check_session_exp(session)
    if dbconn is None:
        await init_db_pool()

    ipui = data.get('ipui') or None      # DECT International Portable User Identity (hex)
    if check_auth_isadmin(session):
        n = await dbconn.execute(
            "UPDATE registered_extensions SET auth_code = $2, switch = $3, ipui = $4, provisioned = 't' WHERE extn = $1",
            int(data['extn']), gen_sip_pw(), DECT_SWITCH, ipui)
    else:
        n = await dbconn.execute(
            "UPDATE registered_extensions SET auth_code = $2, switch = $3, ipui = $4, provisioned = 't' WHERE extn = $1 AND userid = $5",
            int(data['extn']), gen_sip_pw(), DECT_SWITCH, ipui, session['uid'])

    if n != 'UPDATE 1':
        session['error'] = 'Could not activate DECT service; contact support'
        print(f'While applying DECT: {n}')

    raise web.HTTPFound('/')

# --------------------------------------------------------------------------- #
# OMNIDAT node API (token-authed, NOT SAML). Mirrors the edge worker so the
# shadydect daemon (daemon/steak.py) can point at either backend. The *77 flow
# validates + binds a handset here; the portal is the enrollment source of truth.
# --------------------------------------------------------------------------- #
import hmac as _hmac

def _check_api_token(request):
    tok = request.headers.get('X-Steak-Token', '')
    return bool(api_token) and _hmac.compare_digest(tok, api_token)

async def api_extension(request):
    if not _check_api_token(request):
        return web.json_response({'ok': False, 'error': 'unauthorized'}, status=401)
    if dbconn is None:
        await init_db_pool()
    extn = int(request.match_info['extn'])
    row = await dbconn.fetchrow(
        "SELECT extn,name,provisioned,switch,ipui,handset_id FROM registered_extensions WHERE extn=$1", extn)
    if not row:
        return web.json_response({'ok': False, 'error': 'not-enrolled'}, status=404)
    return web.json_response({'ok': True, **dict(row)})

async def api_provision(request):
    if not _check_api_token(request):
        return web.json_response({'ok': False, 'error': 'unauthorized'}, status=401)
    if dbconn is None:
        await init_db_pool()
    data = await request.json()
    extn = int(data['extn'])
    row = await dbconn.fetchrow("SELECT extn,name,auth_code FROM registered_extensions WHERE extn=$1", extn)
    if not row:
        return web.json_response({'ok': False, 'error': 'not-enrolled'}, status=404)
    auth = row['auth_code'] or gen_sip_pw()
    await dbconn.execute(
        "UPDATE registered_extensions SET provisioned='t',switch=$2,ipui=$3,handset_id=$4,auth_code=$5 WHERE extn=$1",
        extn, DECT_SWITCH, data.get('ipui'), data.get('handset_id'), auth)
    return web.json_response({'ok': True, 'extension': str(extn), 'name': row['name'], 'auth_code': auth})

async def api_activate(request):
    # omniDECT handset activation: the camper dials the activation number and
    # enters their activation code on the handset. The node presents that code +
    # the handset's IPUI here; we bind the matching DECT extension. This is the
    # code-as-credential flow (vs api_provision, which trusts a chosen extn).
    if not _check_api_token(request):
        return web.json_response({'ok': False, 'error': 'unauthorized'}, status=401)
    if dbconn is None:
        await init_db_pool()
    data = await request.json()
    code = str(data.get('code', '')).strip()
    if not code:
        return web.json_response({'ok': False, 'error': 'no-code'}, status=400)
    # match an enrolled, not-yet-activated DECT extension by its activation code
    row = await dbconn.fetchrow(
        "SELECT extn,name FROM registered_extensions "
        "WHERE auth_code=$1 AND switch=$2 AND provisioned='f'", code, DECT_SWITCH)
    if not row:
        return web.json_response({'ok': False, 'error': 'bad-code'}, status=404)
    # Mint the extension's SIP secret by *rotating* auth_code: the activation
    # code was a one-time credential the camper keyed on the handset; replacing
    # it (a) issues the real SIP password and (b) makes the code unreplayable.
    # The node maps this to sip.SipAccount and registers to sip_registrar
    # (shadydect ADR 0001, SIP-FP northbound).
    sip_pw = gen_sip_pw()
    await dbconn.execute(
        "UPDATE registered_extensions SET provisioned='t',ipui=$2,handset_id=$3,auth_code=$4 WHERE extn=$1",
        row['extn'], data.get('ipui'), data.get('handset_id'), sip_pw)
    resp = {'ok': True, 'extension': str(row['extn']), 'name': row['name']}
    if sip_registrar:
        resp['sip_password'] = sip_pw
        resp['sip_registrar'] = sip_registrar
    return web.json_response(resp)

async def api_registry(request):
    if not _check_api_token(request):
        return web.json_response({'ok': False, 'error': 'unauthorized'}, status=401)
    if dbconn is None:
        await init_db_pool()
    # auth_code is included for *provisioned* rows only: post-activation it is
    # the extension's SIP password (see api_activate rotation), which the
    # OmniDECT Asterisk needs to render pjsip endpoints. Pre-activation it is
    # the camper's secret activation code and is never disclosed.
    rows = await dbconn.fetch(
        "SELECT extn,name,provisioned,switch,ipui,handset_id,"
        "CASE WHEN provisioned THEN auth_code END AS auth_code "
        "FROM registered_extensions ORDER BY extn")
    return web.json_response({'ok': True, 'extensions': [dict(r) for r in rows]})

async def saml_acs(request):
    req_data = saml_req_data.copy()
    req_data['get_data'] = dict(request.query)
    req_data['post_data'] = dict(await request.post())
    print(f'SAML response was {req_data}')
    auth = OneLogin_Saml2_Auth(req_data, old_settings=SAML_SETTINGS)
    auth.process_response()
    errors = auth.get_errors()
    if errors:
        return web.Response(text=f"SAML errors: {errors}, {auth.get_last_error_reason()}", status=400)
    if not auth.is_authenticated():
        return web.Response(text="Not authenticated", status=403)

    session = await aiohttp_session.new_session(request)
    session['uid'] = auth.get_nameid()
    session['attributes'] = auth.get_attributes()
    session['iat'] = time.time()
    print(f'Logged in user {session}')

    raise web.HTTPFound("/")

async def init_db_pool():
    global dbconn
    # OMNIDAT shares fryos_production Postgres; keep steak tables in their own
    # schema. Setting search_path lets upstream's unqualified `registered_extensions`
    # queries resolve to steak.registered_extensions without touching omnidat/fryos.
    server_settings = {}
    db_schema = config.get('db_schema', '')
    if db_schema:
        server_settings['search_path'] = db_schema
    dbconn = await asyncpg.create_pool(dsn=dbconnstr, server_settings=server_settings or None)
    print(f'dbconn: {dbconn} (search_path={db_schema or "default"})')

async def init_saml_settings():
    global SAML_SETTINGS
    remote_md = OneLogin_Saml2_IdPMetadataParser.parse_remote(IDP_METADATA)
    del remote_md['sp']
    SAML_SETTINGS.update(remote_md)
    print(f'samlsettings: {SAML_SETTINGS}')

if __name__ == '__main__':
    app = web.Application()
    aiohttp_session.setup(app, EncryptedCookieStorage(
        cookiekey,
        cookie_name='session',
        secure=True,
        samesite='strict'
    ))

    aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader(os.path.join(os.getcwd(), 'templates')))

    app.add_routes([web.post('/saml/acs', saml_acs)])
    #app.add_routes([web.get('/saml/service-provider-metadata', saml_metadata)])

    app.add_routes([web.get('/', homepage)])
    app.add_routes([web.get('/directory', directory)])
    app.add_routes([web.get('/api/directory.json', directory_json)])

    app.add_routes([web.post('/rename_extn', rename_extn)])
    app.add_routes([web.post('/delete_extn', delete_extn)])
    app.add_routes([web.post('/create_extn', create_extn)])
    app.add_routes([web.post('/publish_extn', publish_extn)])
    app.add_routes([web.post('/prov_to_sip', prov_to_sip)])
    app.add_routes([web.post('/prov_to_dect', prov_to_dect)])  # OMNIDAT / omniDECT
    # OMNIDAT node API (token-authed), parity with the edge worker
    app.add_routes([web.get('/api/extension/{extn}', api_extension)])
    app.add_routes([web.post('/api/provision', api_provision)])
    app.add_routes([web.post('/api/activate', api_activate)])  # omniDECT code activation
    app.add_routes([web.get('/api/registry', api_registry)])

    app.add_routes([web.static('/static', os.path.join(os.getcwd(), 'static'))])

    # OMNIDAT staging: skip_saml_init lets the node run before the SAML IdP SP is
    # registered — the token-authed node API (/api/*) and the public /directory
    # serve normally; only the SAML-gated human UI is unavailable until the IdP
    # metadata is filled in and this flag is removed.
    if config.get('skip_saml_init'):
        print('WARNING: skip_saml_init set — SAML human UI disabled; API + directory only')
    else:
        asyncio.run(init_saml_settings())

    # OMNIDAT: bind a TCP port when listen_port is set (so the ForgeGraph edge
    # Caddy can reverse-proxy to it), else the upstream unix socket.
    listen_port = config.get('listen_port')
    if listen_port:
        web.run_app(app, host=config.get('listen_host', '0.0.0.0'), port=int(listen_port))
    else:
        try:
            os.unlink(socketpath)
        except:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(socketpath)
        os.chmod(socketpath, 0o666)
        web.run_app(app, sock=sock)

# vim: set ts=4 sw=4 expendtab
