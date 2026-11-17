"""Concrete field interactions, deliberately NOT a live Hub provider.

Public ``HubDraftFormWriter.transfer`` refuses before touching the driver. Only
``bind_synthetic_fixture_for_tests`` constructs the private executor, which is
permanently restricted to a fresh nonce-bound localhost Chrome fixture. There
is no live-enabled flag, UI toggle, Save, Submit, Create, Archive or Publish.

This is a partial form-transfer slice, not MutationRequest/HubAdapter creation.
DOM checks are non-atomic, field events may autosave, and a failed interaction
can leave partial changes. No retry/rollback is attempted. Raw values never
appear in the report. See tests/HUB_FORM_WRITER.md for qualification gaps.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit

from .date_capture import DATE_CAPTURE_HELPERS, DATE_NAMES, date_observed_parts, segmented_date_record_valid

TEXT_FIELDS = {
    "details": ("land_area", "floor_area", "street_number", "street_name", "street_type", "estate_name",
                "lightstone_erf_number", "lightstone_sectional_scheme_unit_number", "scheme_number", "scheme_name",
                "lightstone_id_reg_number", "door_number", "latitude", "longitude", "selling_price", "rates", "levy", "special_levies"),
    "deal_particulars": ("total_commission_value", "total_commission_percentage"),
    "media": ("virtualtour_url", "videotour_url", "p24_youtube_video_id", "matterport_video_id", "eyespy360_url", "marketing_header"),
}
COMBO_NAMES = ("property_type_id", "mandate_type_id", "office_id", "listing_type_id", "country_id", "land_area_units",
               "floor_area_units", "apartment_descriptive_property_type", "deed_office_id")
CHECKBOX_NAMES = ("poa", "featured", "address_hidden", "transfer_cost")
COUNT_IDS = tuple("feature-" + part + "-input" for part in ("bedroom", "kitchen", "bathroom", "garage", "parking", "lounge", "dining"))
ADDRESS_NAMES = frozenset(("street_number", "street_name", "street_type", "estate_name", "door_number"))
CONTROL, NULL, TAB, ESCAPE = "\ue009", "\ue000", "\ue004", "\ue00c"
_USED_NONCES: set[str] = set()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


class TransferRejected(ValueError):
    """Static, non-private failure code, suitable for a report."""


@dataclass(frozen=True, slots=True)
class TransferContext:
    operation_id: str
    account_id: str
    agency_id: str
    source_id: str
    source_rxcu: str
    expected_source_revision: str
    source_capture_sha256: str
    approved_address_sha256: str
    draft_id: str
    expected_draft_revision: str
    page: str = "details"

    def __post_init__(self):
        if any(not isinstance(v, str) or not v or len(v) > 128 for v in asdict(self).values()):
            raise TransferRejected("invalid_context")
        if (self.page not in TEXT_FIELDS or self.source_id == self.draft_id
                or not re.fullmatch(r"[0-9]{1,128}", self.source_id)
                or not re.fullmatch(r"[0-9]{1,128}", self.draft_id)
                or not re.fullmatch(r"RXCU[0-9]{3,20}", self.source_rxcu)
                or any(not re.fullmatch(r"[a-f0-9]{64}", getattr(self, k)) for k in
                       ("source_capture_sha256", "approved_address_sha256"))):
            raise TransferRejected("invalid_context")


@dataclass(frozen=True, slots=True)
class FieldTransfer:
    key: str
    source_json: str
    baseline_json: str

    @classmethod
    def from_values(cls, key: str, source: Any, baseline: Any) -> "FieldTransfer":
        return cls(key, _json(source), _json(baseline))

    def __post_init__(self):
        if not isinstance(self.key, str):
            raise TransferRejected("invalid_field")
        for raw in (self.source_json, self.baseline_json):
            try:
                if not isinstance(raw, str) or len(raw) > 100000 or _json(json.loads(raw)) != raw:
                    raise ValueError
            except (TypeError, ValueError):
                raise TransferRejected("invalid_field_value") from None

    @property
    def source(self):
        return json.loads(self.source_json)

    @property
    def baseline(self):
        return json.loads(self.baseline_json)


@dataclass(frozen=True, slots=True)
class FormTransferRequest:
    """Immutable partial slice; never sets source completeness to true.

    Source fields are copied exactly. Only explicitly approved address text
    overrides can differ. No suburb, date renewal or unspecified fields exist
    in this API. The binding hashes are context evidence, not authentication.
    """
    context: TransferContext
    fields: tuple[FieldTransfer, ...]
    approved_address_json: str = "{}"

    def __post_init__(self):
        if (type(self.context) is not TransferContext or type(self.fields) is not tuple or not self.fields
                or any(type(f) is not FieldTransfer for f in self.fields)
                or len({f.key for f in self.fields}) != len(self.fields)):
            raise TransferRejected("invalid_request")
        try:
            address = json.loads(self.approved_address_json)
            if (not isinstance(address, dict) or _json(address) != self.approved_address_json
                    or not set(address).issubset(ADDRESS_NAMES)
                    or not set(address).issubset(f.key for f in self.fields)
                    or _digest(address) != self.context.approved_address_sha256):
                raise ValueError
        except (TypeError, ValueError):
            raise TransferRejected("invalid_address_binding") from None
        for field in self.fields:
            _validate_value(field.key, field.source, self.context.page)
            _validate_value(field.key, field.baseline, self.context.page)
            _validate_value(field.key, address.get(field.key, field.source), self.context.page)

    def desired(self, field: FieldTransfer):
        return json.loads(self.approved_address_json).get(field.key, field.source)

    @property
    def fingerprint(self):
        return _digest(asdict(self))


def _kind(key: str, page: str) -> str:
    if page == "media" and key == "marketing_header":
        return "textarea"
    if key in TEXT_FIELDS[page]:
        return "text"
    if page == "details":
        if key in COMBO_NAMES: return "combo"
        if key in CHECKBOX_NAMES: return "checkbox"
        if key in COUNT_IDS: return "count"
        if key in DATE_NAMES: return "date"
        if key == "sale_state_id": return "radio"
    # Exact observed label and ID must also be checked by the DOM reader.
    if page == "media" and key == "brochure_description": return "textarea"
    raise TransferRejected("unsupported_field")


def _string(value):
    return isinstance(value, str) and len(value) <= 50000 and not any(
        ord(c) < 32 and c not in "\n\t\r" or 0xE000 <= ord(c) <= 0xF8FF for c in value)


def _validate_value(key, value, page):
    kind = _kind(key, page)
    good = False
    if kind in ("text", "textarea", "count"):
        good = _string(value) and (kind == "textarea" or "\n" not in value and "\r" not in value) and "\t" not in value
    elif kind == "checkbox":
        good = type(value) is dict and set(value) == {"checked", "control_value"} and type(value["checked"]) is bool and _string(value["control_value"])
    elif kind == "combo":
        good = type(value) is dict and set(value) == {"code", "text"} and all(_string(x) for x in value.values())
        good = good and bool(re.fullmatch(r"[A-Za-z]{2}" if key == "country_id" else r"[A-Za-z0-9_.:-]{0,128}", value["code"]))
    elif kind == "radio":
        good = (type(value) is list and bool(value) and all(type(x) is dict and set(x) == {"code", "checked"}
                and _string(x["code"]) and type(x["checked"]) is bool for x in value))
        good = good and len({x["code"] for x in value}) == len(value) and sum(x["checked"] for x in value) == 1
    elif kind == "date":
        good = type(value) is dict and set(value) == {"raw", "parts"} and _string(value["raw"])
        parts = value.get("parts") if good else None
        good = good and type(parts) is list and len(parts) == 3
        if good:
            for i, p in enumerate(parts):
                good = good and (type(p) is dict and set(p) == {"dom_order", "aria_label", "text", "aria_valuenow", "aria_valuetext"}
                    and type(p["dom_order"]) is int and p["dom_order"] == i and p["aria_label"] == ("Year", "Month", "Day")[i]
                    and _string(p["text"]) and isinstance(p["aria_valuenow"], str)
                    and bool(re.fullmatch(r"[0-9]{1,8}", p["aria_valuenow"]))
                    and (p["aria_valuetext"] is None if i == 0 else _string(p["aria_valuetext"])))
    if not good:
        raise TransferRejected("unsupported_field_value")


@dataclass(frozen=True, slots=True)
class TransferReport:
    status: str
    code: str
    operation_id: str
    request_hash: str
    verified_fields: tuple[str, ...] = ()
    edited_fields: tuple[str, ...] = ()
    attempted_fields: tuple[str, ...] = ()
    uncertain_field: str | None = None
    form_state_uncertain: bool = False
    live_qualified: bool = False
    complete_recreation: bool = False
    saved: bool | None = None
    commit_action_clicked: bool = False
    persistence_verified: bool = False


class HubDraftFormWriter:
    """Production entry point. No provider has yet been qualified."""
    def __init__(self, driver):
        self.driver = driver

    def transfer(self, request: FormTransferRequest) -> TransferReport:
        return TransferReport("blocked", "live_provider_unqualified", request.context.operation_id, request.fingerprint)


# Read-only DOM. Element references are returned for ordinary WebDriver actions.
# All discovered business controls are accounted for, including unrequested ones.
FORM_STATE_SCRIPT = r"""
return (() => {
  const spec=arguments[0], limitations=new Set();
  const all=(s,q)=>Array.from(s.querySelectorAll(q));
  const visible=e=>{
    if(!e || !e.getClientRects().length) return false;
    for(let p=e;p;p=p.parentElement){const s=getComputedStyle(p);
      if(p.hidden || p.getAttribute('aria-hidden')==='true' || s.display==='none' ||
        ['hidden','collapse'].includes(s.visibility) || Number(s.opacity||1)===0) return false;
    } return true;
  };
  const secret=/password|passwd|passcode|one.?time|(?:^|[_\s-])otp(?:$|[_\s-])|(?:^|[_\s-])pin(?:$|[_\s-])|token|secret|credential|csrf|authorization|credit.?card|card.?number|card.?security|(?:^|[_\s-])cvv(?:$|[_\s-])/i;
  const sensitiveContext=e=>{
    for(let p=e;p;p=p.parentElement){
      const referenced=(p.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' ');
      const labels=p.labels?Array.from(p.labels).map(l=>l.textContent||'').join(' '):'';
      if(p.hasAttribute('data-sensitive')||secret.test([p.id,p.getAttribute('name'),p.getAttribute('autocomplete'),p.getAttribute('aria-label'),referenced,labels].join(' ')))return true;
    }return false;
  };
  const textOf=e=>(e?.textContent||'').replace(/\s+/g,' ').trim();
  const scopeFor=e=>'';
  const cut=(v,n=100000)=>String(v).slice(0,n);
  /* DATE_HELPERS */
  const roots=all(document,'main#quick-relist-synthetic-form');
  const state={href:location.href,ready:document.readyState==='complete',nonce:document.body.dataset.fixtureNonce,
    synthetic:document.body.dataset.syntheticOnly,context:null,fields:{},errors:[],overlays:[]};
  const fail=c=>state.errors.push(c);
  if(roots.length!==1 || !visible(roots[0])){fail('form_root_invalid');return state;}
  const root=roots[0]; state.context=root.getAttribute('data-context');
  if(state.href!==spec.href||state.nonce!==spec.nonce||state.synthetic!=='true'||state.context!==spec.context){fail('fixture_identity_changed');return state;}
  if(all(document,'[aria-busy="true"],[role="progressbar"]').some(visible)) fail('form_busy');
  const overlays=all(document,'[role="listbox"],[role="dialog"],[role="alertdialog"],[role="menu"],dialog[open]').filter(visible);
  state.overlays=overlays.map(e=>({tag:e.tagName,role:e.getAttribute('role'),id:e.id}));
  const seen=new Set(),valueRoleNodes=new Set();
  const unique=e=>!e.id || all(document,'[id]').filter(n=>n.id===e.id).length===1;
  const safe=e=>visible(e) && !sensitiveContext(e) && !e.closest('fieldset:disabled') && unique(e);
  const add=(key,kind,value,e,extra={})=>{
    if(state.fields[key]){fail('duplicate_control');return;}
    state.fields[key]={kind,value,shape:{tag:e.tagName,type:e.getAttribute('type'),id:e.id,name:e.getAttribute('name'),
      disabled:!!e.disabled || e.getAttribute('aria-disabled')==='true',readonly:!!e.readOnly || e.getAttribute('aria-readonly')==='true',
      constraints:Object.fromEntries(['min','max','step','minlength','maxlength','pattern','inputmode','required','aria-required','autocomplete'].map(k=>[k,e.getAttribute(k)])),...extra},element:e};
  };
  for(const key of [...spec.text,...spec.counts]){
    const candidates=all(document,'input,textarea').filter(e=>spec.counts.includes(key)?e.id===key:(e.name===key||e.id===key));
    if(!candidates.length)continue;
    if(candidates.length!==1){fail('duplicate_control');continue;}
    const e=candidates[0];seen.add(e);
    if(!root.contains(e)||!safe(e)||!(e.tagName==='TEXTAREA'||e.tagName==='INPUT'&&['text','number','url','tel'].includes(e.type))
      || e.name && e.name!==key || e.id && e.id!==key && spec.counts.includes(key)
      || (e.name && e.id && spec.text.includes(e.id)&&e.name!==e.id)) {fail('text_shape_invalid');continue;}
    if(spec.counts.includes(key) && (e.tagName!=='INPUT'||e.type!=='text'||e.hasAttribute('name'))) {fail('count_shape_invalid');continue;}
    if(key==='marketing_header' && (e.tagName!=='TEXTAREA'||e.id!==key||e.name!==key||Array.from(e.labels||[]).length!==1||textOf(e.labels[0])!=='Marketing header description')) {fail('textarea_shape_invalid');continue;}
    add(key,spec.counts.includes(key)?'count':key==='marketing_header'?'textarea':'text',e.value,e);
  }
  if(spec.brochure){
    const candidates=all(document,'textarea#outlined-multiline-flexible').filter(visible);
    if(candidates.length){
      const e=candidates[0];seen.add(e);
      if(candidates.length!==1||!root.contains(e)||!safe(e)||Array.from(e.labels||[]).length!==1||textOf(e.labels[0])!=='Brochure Description')fail('textarea_shape_invalid');
      else add('brochure_description','textarea',e.value,e);
    }
  }
  for(const key of spec.checks){
    const candidates=all(document,'input').filter(e=>e.name===key);
    if(!candidates.length)continue;
    if(candidates.length!==1){fail('duplicate_control');continue;}
    const e=candidates[0];seen.add(e);
    const label=e.closest('label'),labels={poa:'Price on application',featured:'Featured',address_hidden:'Show address on web',transfer_cost:'No Transfer Duty Applicable'};
    if(!root.contains(e)||!safe(e)||e.type!=='checkbox'||e.indeterminate||!label||!root.contains(label)||label.hasAttribute('for')||all(label,'input').length!==1||!e.classList.contains('PrivateSwitchBase-input')||!e.parentElement?.matches('span.MuiCheckbox-root')||e.parentElement.parentElement!==label||textOf(label)!==labels[key]){fail('checkbox_shape_invalid');continue;}
    add(key,'checkbox',{checked:e.checked,control_value:e.value},e,{label:textOf(label)});
  }
  const radios=all(document,'input').filter(e=>e.name==='sale_state_id');
  if(spec.radios&&radios.length){
    const groups=all(document,'div[role="radiogroup"][aria-label="sale_state_id"]');
    radios.forEach(e=>seen.add(e));
    if(groups.length!==1||!root.contains(groups[0])||radios.length!==5||radios.some(e=>!root.contains(e)||!safe(e)||e.type!=='radio'||!/^\d+$/.test(e.value)||!e.closest('label')||!root.contains(e.closest('label'))||e.closest('label').hasAttribute('for')||all(e.closest('label'),'input').length!==1||!e.classList.contains('PrivateSwitchBase-input')||!e.parentElement?.matches('span.MuiRadio-root')||e.parentElement.parentElement!==e.closest('label')||e.closest('label').parentElement!==groups[0])||new Set(radios.map(e=>e.value)).size!==radios.length||radios.filter(e=>e.checked).length!==1) fail('radio_shape_invalid');
    else {add('sale_state_id','radio',radios.map(e=>({code:e.value,checked:e.checked})),radios[0],
      {members:radios.map(e=>({id:e.id,code:e.value,label:textOf(e.closest('label')),disabled:e.disabled,readonly:e.readOnly}))});state.fields.sale_state_id.elements=radios;}
  }
  for(const key of spec.combos){
    const combos=all(document,'[id]').filter(e=>e.id==='mui-component-select-'+key);
    const inputs=all(document,'input').filter(e=>e.name===key||e.id===key);
    if(!combos.length&&!inputs.length)continue;
    inputs.forEach(e=>seen.add(e));
    if(combos.length!==1||inputs.length!==1){fail('combo_binding_invalid');continue;}
    const e=combos[0],n=inputs[0];
    if(!root.contains(e)||!safe(e)||e.tagName!=='DIV'||e.getAttribute('role')!=='combobox'||n.name!==key||n.id!==key||!unique(n)
      ||n!==e.nextElementSibling||n.parentElement!==e.parentElement||n.type!=='text'||n.hasAttribute('type')
      ||sensitiveContext(n)||n.getAttribute('aria-hidden')!=='true'||n.getAttribute('tabindex')!=='-1'||n.hidden||!textOf(e)) {fail('combo_shape_invalid');continue;}
    valueRoleNodes.add(e);
    add(key,'combo',{code:n.value,text:textOf(e)},e,{disabled:!!n.disabled||e.getAttribute('aria-disabled')==='true',readonly:!!n.readOnly,
      native_id:n.id,native_name:n.name,haspopup:e.getAttribute('aria-haspopup')});
  }
  const dates=dateFieldsFor(root);
  for(const f of dates){
    if(!spec.dates.includes(f.name)){fail('unsupported_date');continue;}
    const n=all(root,'input').find(e=>e.name===f.name);seen.add(n);
    const parts=all(n.parentElement,'[role="spinbutton"]');parts.forEach(e=>valueRoleNodes.add(e));
    add(f.name,'date',{raw:f.value,parts:f.date_picker.segments.map(p=>Object.fromEntries(['dom_order','aria_label','text','aria_valuenow','aria_valuetext'].map(k=>[k,p[k]])))},n,
      {picker_id:n.id,label_id:f.date_picker.label.id});
    state.fields[f.name].date_record=f;state.fields[f.name].elements=parts;
  }
  if([...limitations].some(c=>!['date_semantics_unresolved','date_business_validation_unverified','date_requiredness_unverified'].includes(c)))fail('date_shape_invalid');
  const valueRoles=new Set(['combobox','checkbox','radio','switch','slider','spinbutton','listbox','textbox','searchbox','tree','treeitem','grid','gridcell','option']);
  for(const e of all(root,'[role]')) if(e.getAttribute('role').split(/\s+/).some(role=>valueRoles.has(role))&&!valueRoleNodes.has(e))fail('unsupported_value_control');
  for(const e of all(root,'[aria-pressed],[aria-checked],[aria-valuenow],[aria-valuetext]')) if(!valueRoleNodes.has(e))fail('unsupported_value_control');
  for(const e of all(root,'input,textarea,select')){
    if(!seen.has(e))fail('unsupported_control');
  }
  for(const e of all(root,'[contenteditable]')) if(!dates.some(f=>all(root,'input').find(n=>n.name===f.name)?.parentElement.contains(e)))fail('unsupported_editable');
  return state;
})();
""".replace("/* DATE_HELPERS */", DATE_CAPTURE_HELPERS)

# Only property_type_id menu structure was observed live. The other eight
# menus use this structure in the synthetic fixture ONLY, never a live claim.
MENU_STATE_SCRIPT = r"""
return (()=>{
 const key=arguments[0],visible=e=>{if(!e||!e.getClientRects().length)return false;for(let p=e;p;p=p.parentElement){const s=getComputedStyle(p);if(p.hidden||p.getAttribute('aria-hidden')==='true'||s.display==='none'||['hidden','collapse'].includes(s.visibility)||Number(s.opacity||1)===0)return false;}return true;};
 const combos=[...document.querySelectorAll('[id]')].filter(e=>e.id==='mui-component-select-'+key);
 if(combos.length!==1)return null;
 const c=combos[0],id=c.getAttribute('aria-controls');
 const lists=[...document.querySelectorAll('[role="listbox"]')].filter(visible);
 const nodes=[...document.querySelectorAll('[id]')].filter(e=>e.id===id);
 if(!id||c.getAttribute('aria-expanded')!=='true'||c.getAttribute('aria-haspopup')!=='listbox'||lists.length!==1||nodes.length!==1||lists[0]!==nodes[0]||lists[0].tagName!=='UL')return null;
 const list=lists[0],paper=list.parentElement,popup=paper?.parentElement,options=[...list.querySelectorAll('[role="option"]')];
 if(list.getAttribute('tabindex')!=='-1'||!list.classList.contains('MuiMenu-list')||!paper?.matches('div.MuiMenu-paper')||!popup?.matches('div.MuiMenu-root.MuiModal-root')||popup.id!=='menu-'+key||popup.getAttribute('role')!=='presentation'||[...document.querySelectorAll('[id]')].filter(e=>e.id===popup.id).length!==1)return null;
 if(!options.length||list.children.length!==options.length||options.some(e=>e.tagName!=='LI'||!e.classList.contains('MuiMenuItem-root')||e.hasAttribute('id')||e.hasAttribute('value')||e.parentElement!==list||!visible(e)||!e.hasAttribute('data-value')||!['-1','0'].includes(e.getAttribute('tabindex'))||![null,'true','false'].includes(e.getAttribute('aria-disabled'))||!['true','false'].includes(e.getAttribute('aria-selected'))))return null;
 return {id,options:options.map(e=>({code:e.getAttribute('data-value'),text:e.textContent.replace(/\s+/g,' ').trim(),
  selected:e.getAttribute('aria-selected')==='true',disabled:e.getAttribute('aria-disabled')==='true',element:e}))};
})();
"""


def _spec(page):
    return {"text": list(TEXT_FIELDS[page]), "counts": list(COUNT_IDS) if page == "details" else [],
            "checks": list(CHECKBOX_NAMES) if page == "details" else [], "combos": list(COMBO_NAMES) if page == "details" else [],
            "dates": list(DATE_NAMES) if page == "details" else [], "radios": page == "details", "brochure": page == "media"}


def _snapshot(state):
    return {key: {"kind": f["kind"], "value": f["value"], "shape": f["shape"]} for key, f in state["fields"].items()}


def _local_url(url, nonce, context):
    try:
        u = urlsplit(url)
        return (isinstance(url, str) and u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost")
                and u.port is not None and 1024 <= u.port <= 65535 and not u.username and not u.password
                and not u.query and not u.fragment and "?" not in url and "#" not in url and "\\" not in url
                and u.path == f"/quick-relist-fixture/{nonce}/draft/{context.draft_id}/{context.page}")
    except (TypeError, ValueError):
        return False


def bind_synthetic_fixture_for_tests(driver, request: FormTransferRequest, *, nonce: str):
    """Explicit one-shot local test seam, NOT a live provider registration API.

    The harness must freshly serve its own synthetic-only document with this
    nonce and exact context. No arbitrary URL, root selector or driver guard
    can be supplied to relax checks. The nonce is consumed even if bind fails.
    """
    if type(request) is not FormTransferRequest or not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{64}", nonce):
        raise TransferRejected("fixture_binding_invalid")
    if nonce in _USED_NONCES:
        raise TransferRejected("fixture_nonce_already_used")
    _USED_NONCES.add(nonce)
    try:
        if driver.capabilities.get("browserName", "").lower() != "chrome":
            raise TransferRejected("fixture_requires_chrome")
        url = driver.current_url
        if not _local_url(url, nonce, request.context):
            raise TransferRejected("fixture_origin_or_route_rejected")
        writer = _SyntheticFormWriter(driver, request, nonce, url)
        initial = writer._read()
        writer._expected = _snapshot(initial)
        writer._nodes = writer._node_refs(initial)
        for field in request.fields:
            if field.key not in writer._expected or writer._expected[field.key]["value"] != field.baseline:
                raise TransferRejected("initial_baseline_mismatch")
        return writer
    except TransferRejected:
        raise
    except Exception:
        raise TransferRejected("fixture_inspection_failed") from None


class _SyntheticFormWriter:
    def __init__(self, driver, request, nonce, url):
        self._driver, self._request, self._nonce, self._url = driver, request, nonce, url
        self._handle = driver.current_window_handle
        self._handles = tuple(sorted(driver.window_handles))
        self._expected = None
        self._nodes = None
        self._menu_open = False
        self._used = False
        self._verified, self._edited, self._attempted = [], [], []
        self._uncertain = None

    @staticmethod
    def _node_refs(state):
        return {key: (f["element"], tuple(f.get("elements", ()))) for key, f in state["fields"].items()}

    def _read(self, *, menu=False):
        d, r = self._driver, self._request
        if (d.current_window_handle != self._handle or tuple(sorted(d.window_handles)) != self._handles
                or d.current_url != self._url or not _local_url(d.current_url, self._nonce, r.context)):
            raise TransferRejected("fixture_identity_changed")
        s = d.execute_script(FORM_STATE_SCRIPT, dict(_spec(r.context.page), href=self._url, nonce=self._nonce, context=_json(asdict(r.context))))
        if (not isinstance(s, dict) or s.get("href") != self._url or s.get("ready") is not True
                or s.get("nonce") != self._nonce or s.get("synthetic") != "true"
                or s.get("context") != _json(asdict(r.context))):
            raise TransferRejected("fixture_identity_changed")
        if s.get("errors") or not isinstance(s.get("fields"), dict):
            raise TransferRejected("form_structure_rejected")
        if s.get("overlays") and (not menu or len(s["overlays"]) != 1 or s["overlays"][0]["role"] != "listbox"):
            raise TransferRejected("unexpected_overlay")
        if self._nodes is not None and self._node_refs(s) != self._nodes:
            raise TransferRejected("control_nodes_changed")
        for key, f in s["fields"].items():
            _validate_value(key, f["value"], r.context.page)
            if f["kind"] == "date" and not segmented_date_record_valid(f.get("date_record")):
                raise TransferRejected("date_structure_rejected")
        # Check browser context again after the non-atomic DOM read.
        if d.current_url != self._url or d.current_window_handle != self._handle or tuple(sorted(d.window_handles)) != self._handles:
            raise TransferRejected("fixture_identity_changed")
        return s

    def _guard(self, *, menu=False):
        s = self._read(menu=menu)
        if self._expected is not None and _snapshot(s) != self._expected:
            raise TransferRejected("baseline_changed")
        return s

    def _after(self, key, expected_value=None, *, intermediate_date=False):
        s = self._read()
        current = _snapshot(s)
        previous = self._expected
        if set(current) != set(previous) or any(current[k] != previous[k] for k in current if k != key):
            raise TransferRejected("unrelated_field_changed")
        if current[key]["shape"] != previous[key]["shape"] or current[key]["kind"] != previous[key]["kind"]:
            raise TransferRejected("control_shape_changed")
        if not intermediate_date and current[key]["value"] != expected_value:
            raise TransferRejected("field_readback_mismatch")
        self._expected = current
        return s

    def _menu(self, key, expected=None):
        self._guard(menu=True)
        menu = self._driver.execute_script(MENU_STATE_SCRIPT, key)
        if not isinstance(menu, dict) or not isinstance(menu.get("options"), list):
            raise TransferRejected("menu_structure_rejected")
        options = menu["options"]
        if (len({o["code"] for o in options}) != len(options) or sum(o["selected"] for o in options) != 1
                or any(o["selected"] and {"code": o["code"], "text": o["text"]} != self._expected[key]["value"] for o in options)):
            raise TransferRejected("menu_options_ambiguous")
        signature = [{k: v for k, v in o.items() if k != "element"} for o in options]
        if expected is not None and signature != expected:
            raise TransferRejected("menu_options_changed")
        return options, signature

    def _open_menu(self, key):
        s = self._guard()
        self._menu_open = True  # Set before the possibly executed click.
        s["fields"][key]["element"].click()
        return self._menu(key)

    def _close_menu(self):
        self._guard(menu=True)
        self._driver.switch_to.active_element.send_keys(ESCAPE)
        self._guard()
        self._menu_open = False

    def _check_editable(self, s, field):
        f = s["fields"].get(field.key)
        if not f or f["kind"] != _kind(field.key, self._request.context.page):
            raise TransferRejected("requested_control_missing")
        shape = f["shape"]
        if shape["disabled"] or shape["readonly"] or any(x["disabled"] or x["readonly"] for x in shape.get("members", [])):
            raise TransferRejected("requested_control_disabled")
        desired = self._request.desired(field)
        if f["kind"] == "checkbox" and desired["control_value"] != f["value"]["control_value"]:
            raise TransferRejected("checkbox_control_value_changed")
        if f["kind"] == "radio" and [v["code"] for v in desired] != [v["code"] for v in f["value"]]:
            raise TransferRejected("radio_options_changed")

    def transfer(self) -> TransferReport:
        r = self._request
        if self._used:
            return TransferReport("blocked", "fixture_transfer_already_attempted", r.context.operation_id, r.fingerprint,
                                  form_state_uncertain=bool(self._attempted) or self._menu_open)
        self._used = True
        menus = {}
        code, status = "no_commit_action_clicked_persistence_unqualified", "supported_fields_verified"
        try:
            state = self._guard()
            # Entire requested batch is structurally validated before any edits.
            for f in r.fields:
                self._check_editable(state, f)
            # Discover every requested option before field writes; popup opens
            # are ordinary UI interactions, not asserted to be server-read-only.
            for f in r.fields:
                if _kind(f.key, r.context.page) == "combo" and r.desired(f) != f.baseline:
                    options, signature = self._open_menu(f.key)
                    desired = r.desired(f)
                    matches = [o for o in options if o["code"] == desired["code"] and o["text"] == desired["text"] and not o["disabled"]]
                    self._close_menu()
                    if len(matches) != 1: raise TransferRejected("requested_option_unavailable")
                    menus[f.key] = signature
            for f in r.fields:
                state = self._guard()
                self._check_editable(state, f)
                desired, key = r.desired(f), f.key
                if state["fields"][key]["value"] == desired:
                    self._verified.append(key)
                    continue
                self._attempted.append(key)
                self._uncertain = key
                kind = _kind(key, r.context.page)
                element = state["fields"][key]["element"]
                if kind in ("text", "textarea", "count"):
                    element.clear()
                    self._after(key, "")
                    if desired:
                        element = self._guard()["fields"][key]["element"]
                        element.send_keys(desired)
                        self._after(key, desired)
                    # Blur through ordinary keyboard navigation, then recheck.
                    self._guard()["fields"][key]["element"].send_keys(TAB)
                    self._after(key, desired)
                elif kind == "checkbox":
                    element.click()
                    self._after(key, desired)
                elif kind == "radio":
                    index = next(i for i, v in enumerate(desired) if v["checked"])
                    state["fields"][key]["elements"][index].click()
                    self._after(key, desired)
                elif kind == "combo":
                    self._open_menu(key)
                    options, _ = self._menu(key, menus[key])
                    option = next(o for o in options if o["code"] == desired["code"] and o["text"] == desired["text"] and not o["disabled"])
                    # Menu provenance and full baseline were rechecked above.
                    option["element"].click()
                    self._after(key, desired)
                    self._menu_open = False
                elif kind == "date":
                    for index, part in enumerate(desired["parts"]):
                        state = self._guard()
                        if state["fields"][key]["value"]["parts"][index] == part: continue
                        state["fields"][key]["elements"][index].click()
                        state = self._guard()
                        state["fields"][key]["elements"][index].send_keys(CONTROL, "a", NULL, part["aria_valuenow"], TAB)
                        state = self._after(key, intermediate_date=True)
                        # Detect clamping and silent changes to earlier parts.
                        if state["fields"][key]["value"]["parts"][:index+1] != desired["parts"][:index+1]:
                            raise TransferRejected("date_part_readback_mismatch")
                    self._after(key, desired)
                self._verified.append(key)
                self._edited.append(key)
                self._uncertain = None
            self._guard()
        except TransferRejected as exc:
            code = str(exc)
            status = "partial_or_uncertain" if self._attempted else "blocked"
        except Exception:
            code = "browser_interaction_uncertain" if self._attempted else "browser_preflight_failed"
            status = "partial_or_uncertain" if self._attempted else "blocked"
        return TransferReport(status, code, r.context.operation_id, r.fingerprint, tuple(self._verified), tuple(self._edited),
                              tuple(self._attempted), self._uncertain, status == "partial_or_uncertain" or self._menu_open)
