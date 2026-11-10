"""Synthetic-only normal file selection. Never a registered/live Hub provider.

File selection may upload immediately. A send attempt is never retried, even
when a browser error prevents readback. Receipt evidence below belongs only to
the localhost application model; no Hub persistence capability is asserted.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .hub_form_writer import TransferContext, TransferRejected, _json, _digest, _local_url

ACCEPT = 'image/png,image/jpg,image/jpeg,image/svg,image/gif,image/tif'
KINDS = ('photo', 'floorplan')
GAPS = ('hub_upload_unqualified', 'hub_persistence_unqualified', 'global_order_unqualified',
        'caption_cover_rotation_tags_unsupported', 'full_fidelity_unverified')
_USED_NONCES = set()


def _id(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value))


@dataclass(frozen=True, slots=True)
class MediaFile:
    media_id: str
    kind: str
    position: int
    size: int
    sha256: str
    local_path: str
    source_evidence_json: str
    metadata_json: str = '{}'

    def __post_init__(self):
        if (not isinstance(self.metadata_json, str) or len(self.metadata_json.encode('utf-8')) > 8192
                or not isinstance(self.source_evidence_json, str) or len(self.source_evidence_json.encode('utf-8')) > 2048):
            raise TransferRejected('media_metadata_limit')
        try:
            metadata = json.loads(self.metadata_json)
            valid = (type(metadata) is dict and _json(metadata) == self.metadata_json)
        except (ValueError, TypeError):
            valid = False
        if (not _id(self.media_id) or self.kind not in KINDS or type(self.position) is not int
                or self.position < 0 or type(self.size) is not int or not 0 < self.size <= 32*1024*1024
                or not isinstance(self.sha256, str) or not re.fullmatch('[a-f0-9]{64}', self.sha256)
                or not isinstance(self.local_path, str) or not os.path.isabs(self.local_path)
                or any(ord(c) < 32 or 0xE000 <= ord(c) <= 0xF8FF for c in self.local_path)
                or Path(self.local_path).suffix.lower() not in ('.png', '.jpg', '.jpeg') or not valid):
            raise TransferRejected('invalid_media_manifest')


def empty_baseline():
    """Exact supported synthetic empty-target baseline, not a Hub capture."""
    return {'controls': {k: {'accept': ACCEPT, 'multiple': True, 'disabled': False} for k in KINDS},
            'cards': [], 'receipts': [], 'selected': {k: [] for k in KINDS}}


@dataclass(frozen=True, slots=True)
class MediaTransferRequest:
    context: TransferContext
    parent_operation_id: str
    parent_hash: str
    files: tuple[MediaFile, ...]
    baseline_json: str

    def __post_init__(self):
        if (type(self.context) is not TransferContext or self.context.page != 'media'
                or not _id(self.parent_operation_id) or not isinstance(self.parent_hash, str)
                or not re.fullmatch('[a-f0-9]{64}', self.parent_hash)
                or type(self.files) is not tuple or not 0 < len(self.files) <= 100
                or any(type(f) is not MediaFile for f in self.files)):
            raise TransferRejected('invalid_media_request')
        if (sum(f.size for f in self.files) > 128*1024*1024
                or sum(len(f.metadata_json.encode('utf-8')) for f in self.files) > 128*1024):
            raise TransferRejected('media_request_limit')
        for f in self.files:
            evidence = {'asset_id': f.media_id, 'source_id': self.context.source_id,
                        'source_revision': self.context.expected_source_revision,
                        'source_capture_sha256': self.context.source_capture_sha256,
                        'kind': f.kind, 'position': f.position, 'sha256': f.sha256}
            if f.source_evidence_json != _json(evidence):
                raise TransferRejected('source_media_binding_unresolved')
        for attr in ('media_id', 'sha256', 'local_path'):
            if len({getattr(f, attr) for f in self.files}) != len(self.files):
                raise TransferRejected('duplicate_media_manifest')
        if [f.position for f in self.files] != list(range(len(self.files))):
            raise TransferRejected('media_order_unresolved')
        # Appending to an existing grid cannot safely establish duplicate or
        # ordering semantics from thumbnail URLs. This slice requires empty.
        if self.baseline_json != _json(empty_baseline()):
            raise TransferRejected('nonempty_or_unsupported_baseline')

    @property
    def fingerprint(self):
        return _digest(asdict(self))


@dataclass(frozen=True, slots=True)
class MediaTransferReport:
    status: str
    code: str
    attempted_ids: tuple[str, ...] = ()
    selected_ids: tuple[str, ...] = ()
    modeled_receipt_ids: tuple[str, ...] = ()
    upload_state_uncertain: bool = False
    saved: bool | None = None
    live_supported: bool = False
    persistence_verified: bool = False
    full_fidelity: bool = False
    gaps: tuple[str, ...] = GAPS


class HubMediaWriter:
    def __init__(self, driver):
        self._driver = driver

    def transfer(self, request):
        return MediaTransferReport('blocked', 'live_provider_unqualified')


# No setters, event dispatch, fetching, storage or application state access.
# Uploaded-model receipts are explicit fixture DOM evidence, not thumbnails.
MEDIA_STATE_SCRIPT = r"""
return (()=>{
 const q=(r,s)=>[...r.querySelectorAll(s)], root=document.getElementById('synthetic-media');
 if(!root||q(document,'#synthetic-media').length!==1)return null;
 const expected=arguments[1];
 if(location.href!==expected.href||root.dataset.nonce!==expected.nonce||root.dataset.context!==expected.context
  ||root.dataset.request!==expected.request||root.dataset.synthetic!=='true'||root.dataset.ready!=='true')return null;
 const result={href:location.href,nonce:root.dataset.nonce,context:root.dataset.context,
 synthetic:root.dataset.synthetic,request:root.dataset.request,ready:root.dataset.ready,
 controls:{},elements:{},files:{},cards:[],receipts:[],errors:[]};
 const fail=()=>result.errors.push('structure');
 const safe=e=>{for(let p=e;p;p=p.parentElement){const s=getComputedStyle(p);if(p.hidden||p.inert||p.getAttribute('aria-hidden')==='true'||p.getAttribute('aria-disabled')==='true'||s.display==='none'||['hidden','collapse'].includes(s.visibility)||Number(s.opacity||1)===0)return false;}return !!e.getClientRects().length;};
 if(q(document,'iframe,dialog,[contenteditable],button,[aria-pressed],[aria-checked],[aria-valuenow],[aria-valuetext]').length)fail();
 const roles=new Set(['dialog','alertdialog','combobox','listbox','checkbox','radio','switch','slider','spinbutton','textbox','searchbox','tree','treeitem','grid','gridcell','option','button']);
 if(q(document,'[role]').some(e=>e.getAttribute('role').split(/\s+/).some(r=>roles.has(r))))fail();
 const all=q(document,'input,textarea,select,[role="combobox"],[role="listbox"],[role="checkbox"],[role="radio"],[role="switch"],[role="slider"],[role="spinbutton"],[role="textbox"]');
 if(all.length!==2)fail();
 for(const [kind,title] of [['photo','Listing images'],['floorplan','Floor plan images']]){
  const headings=q(document,'h6').filter(e=>e.textContent.trim()===title);
  if(headings.length!==1){fail();continue;}
  const box=headings[0].parentElement, wrappers=q(box,'div.rh__fileInput'), inputs=q(box,'input');
  if(!root.contains(box)||!box.matches('div.MuiBox-root')||!safe(box)||wrappers.length!==1||inputs.length!==1){fail();continue;}
  const e=inputs[0],w=wrappers[0];
  if(e.parentElement!==w||w.parentElement!==box||e.type!=='file'||e.getAttribute('type')!=='file'||!safe(e)||e.disabled||e.matches(':disabled')||e.readOnly||!e.multiple||e.getAttribute('accept')!==arguments[0]
   ||['id','name','aria-label','role','hidden','style','tabindex'].some(a=>e.hasAttribute(a))
   ||!q(box,'div').some(d=>d.textContent.trim()==='Drag & drop files or click to upload files')){fail();continue;}
  result.controls[kind]={accept:e.getAttribute('accept'),multiple:e.multiple,disabled:e.disabled};
  result.elements[kind]=e; result.files[kind]=Array.from(e.files).map(f=>({name:f.name,size:f.size,type:f.type,lastModified:f.lastModified}));
 }
 const grids=q(root,'[data-shared-grid]');if(grids.length!==1)fail();
 for(const e of q(document,'img')){
  if(!grids[0]?.contains(e)||e.alt!=='preview'||!/^image-[A-Za-z0-9_-]+$/.test(e.id))fail();
  result.cards.push({id:e.id,pos:e.getAttribute('pos'),src:e.getAttribute('data-full-src')});
 }
 const receipts=q(root,'[data-modeled-receipt]');
 for(const e of receipts){try{result.receipts.push(JSON.parse(e.textContent));}catch(_){fail();}}
 return result;
})();
"""

# Reads File references and their actual bytes, never changes input.files.
FILE_BYTES_SCRIPT = r"""
const e=arguments[0],binding=arguments[1],expected=arguments[2],done=arguments[arguments.length-1];
const root=document.getElementById('synthetic-media');
const bound=()=>root&&document.querySelectorAll('#synthetic-media').length===1&&document.getElementById('synthetic-media')===root
 &&location.href===binding.href&&root.dataset.nonce===binding.nonce&&root.dataset.context===binding.context
 &&root.dataset.request===binding.request&&root.dataset.synthetic==='true'&&root.dataset.ready==='true'
 &&root.contains(e)&&e.isConnected&&e.type==='file'&&!e.disabled&&!e.matches(':disabled');
if(!bound()){done({ok:false,files:[]});return;}
if(!Array.isArray(expected)||e.files.length!==expected.length||expected.length>100){done({ok:false,files:[]});return;}
const files=Array.from(e.files),keys=['name','size','type','lastModified'];
const matches=(f,m)=>m&&keys.every(k=>f[k]===m[k])&&Number.isSafeInteger(f.size)&&f.size>0&&f.size<=32*1024*1024
 &&['image/png','image/jpeg','image/jpg'].includes(f.type)&&Number.isSafeInteger(f.lastModified);
if(!files.every((f,i)=>matches(f,expected[i]))||files.reduce((n,f)=>n+f.size,0)>128*1024*1024){done({ok:false,files:[]});return;}
Promise.all(files.map(async f=>({name:f.name,size:f.size,type:f.type,lastModified:f.lastModified,
 sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await f.arrayBuffer()))).map(b=>b.toString(16).padStart(2,'0')).join('')})))
 .then(x=>done({ok:bound()&&files.length===e.files.length&&files.every((f,i)=>f===e.files[i]&&matches(f,expected[i])),files:x}))
 .catch(()=>done({ok:false,files:[]}));
"""


def _local_file(item):
    """Bounded read with identity/stat/hash checks; no path in exceptions."""
    p = Path(item.local_path)
    try:
        # Refuse symlinks/reparse points in every component.
        for part in (p, *p.parents):
            s = part.lstat()
            if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & 0x400:
                raise TransferRejected('local_file_unsafe')
        before = p.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size != item.size:
            raise TransferRejected('local_file_changed')
        with os.fdopen(os.open(p, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as stream:
            opened = os.fstat(stream.fileno())
            digest, size, prefix = hashlib.sha256(), 0, b''
            while chunk := stream.read(min(64*1024, item.size + 1 - size)):
                size += len(chunk)
                digest.update(chunk)
                if not prefix: prefix = chunk[:8]
                if size > item.size: raise TransferRejected('local_file_changed')
            after = os.fstat(stream.fileno())
        path_after = p.stat()
        # Windows/Python versions can expose different ctime semantics through
        # path stat and handle fstat. Compare ctime only within the same API;
        # retain cross-API identity/size/mtime and available birthtime evidence.
        shared = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        same_api = lambda s: (shared(s), s.st_ctime_ns, getattr(s, 'st_birthtime_ns', None))
        births = [getattr(s, 'st_birthtime_ns', None) for s in (before, opened, after, path_after)]
        if (any(not stat.S_ISREG(s.st_mode) for s in (opened, after, path_after))
                or shared(before) != shared(opened) or shared(after) != shared(path_after)
                or same_api(before) != same_api(path_after) or same_api(opened) != same_api(after)
                or len({birth for birth in births if birth is not None}) > 1 or size != item.size
                or digest.hexdigest() != item.sha256):
            raise TransferRejected('local_file_changed')
        if not (prefix.startswith(b'\x89PNG\r\n\x1a\n') if p.suffix.lower()=='.png' else prefix.startswith(b'\xff\xd8\xff')):
            raise TransferRejected('local_file_format_rejected')
        return (before.st_dev, before.st_ino)
    except TransferRejected:
        raise
    except Exception:
        raise TransferRejected('local_file_unavailable') from None


def bind_synthetic_media_for_tests(driver, request, *, nonce):
    if type(request) is not MediaTransferRequest or not isinstance(nonce, str) or not re.fullmatch('[a-f0-9]{64}', nonce):
        raise TransferRejected('fixture_binding_invalid')
    if nonce in _USED_NONCES:
        raise TransferRejected('fixture_nonce_already_used')
    _USED_NONCES.add(nonce)
    try:
        if driver.capabilities.get('browserName', '').lower() != 'chrome':
            raise TransferRejected('fixture_requires_chrome')
        if not _local_url(driver.current_url, nonce, request.context):
            raise TransferRejected('fixture_origin_or_route_rejected')
        writer = _SyntheticMediaWriter(driver, request, nonce)
        state = writer._read()
        if _json(writer._snapshot(state)) != request.baseline_json:
            raise TransferRejected('initial_baseline_mismatch')
        writer._nodes = state['elements']
        return writer
    except TransferRejected:
        raise
    except Exception:
        raise TransferRejected('fixture_inspection_failed') from None


class _SyntheticMediaWriter:
    def __init__(self, driver, request, nonce):
        self._driver, self._request, self._nonce = driver, request, nonce
        self._url, self._handle = driver.current_url, driver.current_window_handle
        self._handles = tuple(sorted(driver.window_handles))
        self._nodes = None
        self._attempted, self._selected = [], []
        self._used = False
        self._expected_files = {k: [] for k in KINDS}

    def _identity(self):
        d = self._driver
        if (d.current_url != self._url or not _local_url(d.current_url, self._nonce, self._request.context)
                or d.current_window_handle != self._handle or tuple(sorted(d.window_handles)) != self._handles):
            raise TransferRejected('fixture_identity_changed')

    def _read(self):
        self._identity()
        r = self._request
        s = self._driver.execute_script(MEDIA_STATE_SCRIPT, ACCEPT,
            {'href': self._url, 'nonce': self._nonce, 'context': _json(asdict(r.context)), 'request': r.fingerprint})
        self._identity()
        if (not isinstance(s, dict) or s.get('href') != self._url or s.get('nonce') != self._nonce
                or s.get('context') != _json(asdict(r.context)) or s.get('request') != r.fingerprint
                or s.get('synthetic') != 'true' or s.get('ready') != 'true'):
            raise TransferRejected('fixture_identity_changed')
        if (s.get('errors') or _json(s.get('controls')) != _json(empty_baseline()['controls'])
                or set(s.get('elements', {})) != set(KINDS) or set(s.get('files', {})) != set(KINDS)):
            raise TransferRejected('media_structure_rejected')
        if self._nodes is not None and s['elements'] != self._nodes:
            raise TransferRejected('control_nodes_changed')
        return s

    @staticmethod
    def _snapshot(s):
        return {k: s[k] for k in ('controls','cards','receipts')} | {'selected': s['files']}

    def _receipt(self, item):
        return {'media_id': item.media_id, 'upload_id': 'synthetic-upload-'+item.media_id,
                'sha256': item.sha256, 'size': item.size, 'kind': item.kind,
                'source_position': item.position, 'request': self._request.fingerprint}

    def _evidence(self, s):
        allowed = {_json(self._receipt(f)) for f in self._request.files if f.media_id in self._attempted}
        receipts = s['receipts']
        if (not isinstance(receipts, list) or any(_json(x) not in allowed for x in receipts)
                or len({_json(x) for x in receipts}) != len(receipts)):
            raise TransferRejected('modeled_receipt_mismatch')
        expected_cards = [{'id': 'image-'+x['upload_id'], 'pos': str(i),
                           'src': 'synthetic:'+x['upload_id']} for i,x in enumerate(receipts)]
        if _json(s['cards']) != _json(expected_cards):
            raise TransferRejected('shared_grid_mismatch')
        return tuple(x['media_id'] for x in receipts)

    def _verify_selected(self, s):
        # Validate the entire selected-list metadata before any byte allocation.
        for kind in KINDS:
            expected = self._expected_files[kind]
            selected = s['files'][kind]
            if type(selected) is not list or len(selected) != len(expected):
                raise TransferRejected('selected_files_mismatch')
            for metadata,item in zip(selected, expected):
                mimes = ('image/png',) if Path(item.local_path).suffix.lower() == '.png' else ('image/jpeg','image/jpg')
                if (type(metadata) is not dict or set(metadata) != {'name','size','type','lastModified'}
                        or type(metadata['name']) is not str or metadata['name'] != Path(item.local_path).name
                        or type(metadata['size']) is not int or metadata['size'] != item.size
                        or not 0 < metadata['size'] <= 32*1024*1024
                        or type(metadata['type']) is not str or metadata['type'] not in mimes
                        or type(metadata['lastModified']) is not int or not 0 <= metadata['lastModified'] <= 2**53-1):
                    raise TransferRejected('selected_metadata_mismatch')
        binding = {'href': self._url, 'nonce': self._nonce,
                   'context': _json(asdict(self._request.context)), 'request': self._request.fingerprint}
        for kind in KINDS:
            expected = self._expected_files[kind]
            if not expected:
                continue
            before = self._read()
            if _json(before['files']) != _json(s['files']):
                raise TransferRejected('selected_files_changed')
            result = self._driver.execute_async_script(FILE_BYTES_SCRIPT, s['elements'][kind], binding, s['files'][kind])
            self._identity()
            if not isinstance(result, dict) or result.get('ok') is not True or len(result.get('files', [])) != len(expected):
                raise TransferRejected('selected_bytes_unverified')
            for actual,item,metadata in zip(result['files'], expected, s['files'][kind]):
                if _json(actual) != _json(dict(metadata, sha256=item.sha256)):
                    raise TransferRejected('selected_bytes_mismatch')
            # Re-read reference/metadata after asynchronous File hashing.
            again = self._read()
            if _json(again['files']) != _json(s['files']):
                raise TransferRejected('selected_files_changed')

    def _report(self, status, code, receipts=()):
        return MediaTransferReport(status, code, tuple(self._attempted), tuple(self._selected),
                                   tuple(receipts), bool(self._attempted))

    def transfer(self):
        if self._used:
            return self._report('blocked','fixture_transfer_already_attempted')
        self._used = True
        try:
            s = self._read()
            if _json(self._snapshot(s)) != self._request.baseline_json:
                raise TransferRejected('initial_baseline_mismatch')
            identities = [_local_file(f) for f in self._request.files]
            if len(set(identities)) != len(identities):
                raise TransferRejected('duplicate_local_identity')
            for kind in KINDS:
                batch = [f for f in self._request.files if f.kind == kind]
                if not batch: continue
                s = self._read()
                self._evidence(s)
                self._verify_selected(s)
                for f in batch: _local_file(f)
                # Guard immediately before send. The DOM and local path still
                # cannot be made atomic with WebDriver; read back actual bytes.
                s = self._read()
                self._evidence(s)
                if s['files'][kind]: raise TransferRejected('selection_baseline_changed')
                self._expected_files[kind] = batch
                self._attempted.extend(f.media_id for f in batch)
                s['elements'][kind].send_keys('\n'.join(f.local_path for f in batch))
                s = self._read()
                self._verify_selected(s)
                self._selected.extend(f.media_id for f in batch)
            return self.observe()
        except TransferRejected as exc:
            return self._report('partial_or_uncertain' if self._attempted else 'blocked', str(exc))
        except Exception:
            return self._report('partial_or_uncertain' if self._attempted else 'blocked',
                                'browser_interaction_uncertain' if self._attempted else 'browser_preflight_failed')
        except KeyboardInterrupt:
            return self._report('partial_or_uncertain' if self._attempted else 'blocked', 'interrupted')

    def observe(self):
        """Read-only recovery; never sends, retries, clears or infers absence."""
        try:
            s = self._read()
            self._verify_selected(s)
            receipts = self._evidence(s)
            complete = len(receipts) == len(self._request.files)
            return self._report('synthetic_receipts_observed' if complete else 'partial_or_uncertain',
                                'modeled_only_not_hub_persistence' if complete else 'modeled_receipts_pending', receipts)
        except TransferRejected as exc:
            return self._report('partial_or_uncertain' if self._attempted else 'blocked', str(exc))
        except (Exception, KeyboardInterrupt):
            return self._report('partial_or_uncertain' if self._attempted else 'blocked', 'recovery_observation_failed')
