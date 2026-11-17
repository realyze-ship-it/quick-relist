"""Fresh local fixture builder. Synthetic values only; no remote URLs or accounts."""
from dataclasses import asdict
from html import escape
from pathlib import Path
import secrets

from quick_relist.hub_form_writer import (COMBO_NAMES, COUNT_IDS, DATE_NAMES, TEXT_FIELDS, CHECKBOX_NAMES,
    FieldTransfer, FormTransferRequest, TransferContext, _digest, _json)
from quick_relist.date_capture import DATE_LABELS


def date_value(year=2099, month=11, day=19):
    names = ('January','February','March','April','May','June','July','August','September','October','November','December')
    suffix = 'th' if 11 <= day % 100 <= 13 else {1:'st', 2:'nd', 3:'rd'}.get(day % 10, 'th')
    texts = (str(year), names[month-1], str(day) + suffix)
    return {'raw':f'{year:04d}/{month:02d}/{day:02d}', 'parts':[
        {'dom_order':i, 'aria_label':label, 'text':texts[i], 'aria_valuenow':str(value), 'aria_valuetext':None if i == 0 else texts[i]}
        for i,(label,value) in enumerate(zip(('Year','Month','Day'),(year,month,day)))]}


def fixture_request(page='details', *, keys=None):
    before, desired = {}, {}
    for key in TEXT_FIELDS[page]:
        before[key] = 'Synthetic old ' + key
        desired[key] = '  Synthetic new ' + key + '  '
    if page == 'details':
        for key in COUNT_IDS:
            before[key], desired[key] = '0', '03'
        for key in CHECKBOX_NAMES:
            before[key] = {'checked':True,'control_value':'on'}
            desired[key] = {'checked':False,'control_value':'on'}
        before['sale_state_id'] = [{'code':str(i),'checked':i==1} for i in range(1,6)]
        desired['sale_state_id'] = [{'code':str(i),'checked':i==4} for i in range(1,6)]
        for key in COMBO_NAMES:
            before[key] = {'code':'ZA' if key=='country_id' else '1','text':'Synthetic initial '+key}
            desired[key] = {'code':'NZ' if key=='country_id' else '02','text':'Synthetic target '+key}
        for i,key in enumerate(DATE_NAMES):
            before[key] = date_value()
            desired[key] = date_value(2001+i,5,23)  # Historical mandates deliberately stay historical.
    if page == 'media':
        desired['marketing_header']='  Synthetic marketing header\nSecond line\n'
        before['brochure_description']='Synthetic old brochure'
        desired['brochure_description']='  Synthetic new brochure\nSecond line\n'
    keys = list(desired) if keys is None else keys
    source = {key:desired[key] for key in keys}
    context = TransferContext('synthetic-operation-'+secrets.token_hex(8),'synthetic-account','synthetic-agency',
        '111111','RXCU111111','synthetic-source-rev',_digest(source),_digest({}),
        '222222','synthetic-draft-rev',page)
    return FormTransferRequest(context,tuple(FieldTransfer.from_values(key,desired[key],before[key]) for key in keys))


def fixture_document(request, nonce, scenario='normal'):
    config = {'page':request.context.page,'scenario':scenario,'initialText':{
        key:'Synthetic old '+key for key in TEXT_FIELDS[request.context.page]},
        'combos':list(COMBO_NAMES),'counts':list(COUNT_IDS),'dates':list(DATE_NAMES),'dateLabels':list(DATE_LABELS),
        'checkboxLabels':dict(zip(CHECKBOX_NAMES,('Price on application','Featured','Show address on web','No Transfer Duty Applicable'))),
        'options':{key:[{'code':'ZA' if key=='country_id' else '1','text':'Synthetic initial '+key},
                        {'code':'NZ' if key=='country_id' else '02','text':'Synthetic target '+key}]
                   for key in COMBO_NAMES}, 'changedAfter':sum(f.key in COMBO_NAMES and f.source != f.baseline for f in request.fields)}
    template = (Path(__file__).parent/'fixtures'/'hub_form_writer.html').read_text()
    return template.replace('__NONCE__',nonce).replace('__CONTEXT_ATTR__',escape(_json(asdict(request.context)),quote=True)).replace('__CONFIG_JSON__',_json(config).replace('</','<\\/'))
