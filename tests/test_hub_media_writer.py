"""Offline contracts. These doubles do not qualify Selenium or Hub."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import os
from types import SimpleNamespace
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from quick_relist.hub_form_writer import TransferRejected, _json
from quick_relist.hub_media_writer import (HubMediaWriter, MediaFile, MEDIA_STATE_SCRIPT,
    FILE_BYTES_SCRIPT, ACCEPT, bind_synthetic_media_for_tests, _local_file)
from media_transfer_fixture import fixture_request


class Input:
    def __init__(self,driver,kind):self.driver,self.kind=driver,kind
    def send_keys(self,paths):
        d=self.driver;d.sends+=1
        for path in paths.split('\n'):
            data=Path(path).read_bytes();f=next(x for x in d.request.files if x.local_path==path)
            item={'name':Path(path).name,'size':len(data),'type':'image/png','lastModified':1}
            d.state['files'][self.kind].append(item)
            d.hashes[self.kind].append(dict(item,sha256=hashlib.sha256(data).hexdigest()))
            if not d.pending:d.add_receipt(f)
        if d.interrupt:raise KeyboardInterrupt
        if d.throw:raise RuntimeError('PRIVATE PATH MUST NOT LEAK')


class Driver:
    capabilities={'browserName':'chrome'}
    current_window_handle='window-1';window_handles=['window-1']
    def __init__(self,request,nonce):
        self.request=request
        self.current_url=f'http://127.0.0.1:12345/quick-relist-fixture/{nonce}/draft/{request.context.draft_id}/media'
        self.async_reads=0;self.sends=0;self.throw=False;self.interrupt=False;self.pending=False
        self.hashes={'photo':[],'floorplan':[]}
        self.state={'href':self.current_url,'nonce':nonce,'context':_json(asdict(request.context)),
                    'request':request.fingerprint,'synthetic':'true','ready':'true','errors':[],
                    'controls':{k:{'accept':ACCEPT,'multiple':True,'disabled':False} for k in self.hashes},
                    'elements':{k:Input(self,k) for k in self.hashes},'files':{'photo':[],'floorplan':[]},
                    'cards':[],'receipts':[]}
    def add_receipt(self,f):
        r={'media_id':f.media_id,'upload_id':'synthetic-upload-'+f.media_id,'sha256':f.sha256,
           'size':f.size,'kind':f.kind,'source_position':f.position,'request':self.request.fingerprint}
        self.state['cards'].append({'id':'image-'+r['upload_id'],'pos':str(len(self.state['receipts'])),'src':'synthetic:'+r['upload_id']})
        self.state['receipts'].append(r)
    def execute_script(self,script,*args):
        assert script==MEDIA_STATE_SCRIPT
        return {k:(dict(v) if k=='elements' else deepcopy(v)) for k,v in self.state.items()}
    def execute_async_script(self,script,element,binding,expected):
        self.async_reads+=1
        assert script==FILE_BYTES_SCRIPT
        return {'ok':True,'files':deepcopy(self.hashes[element.kind])}


class MediaWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.request=fixture_request(self.tmp.name);self.nonce=secrets.token_hex(32)
        self.driver=Driver(self.request,self.nonce)
    def bind(self):return bind_synthetic_media_for_tests(self.driver,self.request,nonce=self.nonce)
    def test_public_writer_never_accesses_driver_or_files(self):
        class Trap:
            def __getattribute__(self,name):raise AssertionError('access')
        with patch('quick_relist.hub_media_writer._local_file',side_effect=AssertionError):
            self.assertEqual(HubMediaWriter(Trap()).transfer(Trap()).code,'live_provider_unqualified')
    def test_success_preserves_metadata_reports_gaps_and_no_hub_claim(self):
        writer=self.bind();report=writer.transfer()
        self.assertEqual(report.status,'synthetic_receipts_observed')
        self.assertEqual(self.driver.sends,2)
        self.assertEqual(report.modeled_receipt_ids,('asset-0','asset-2','asset-1'))
        self.assertEqual(tuple(f.position for f in self.request.files),(0,1,2))
        self.assertIn('global_order_unqualified',report.gaps)
        self.assertIsNone(report.saved)
        self.assertEqual(set(report.modeled_receipt_ids),{f.media_id for f in self.request.files})
        self.assertTrue(report.upload_state_uncertain)
        self.assertFalse(any((report.saved,report.persistence_verified,report.live_supported,report.full_fidelity)))
        self.assertIn('caption_cover_rotation_tags_unsupported',report.gaps)
        self.assertIn('Synthetic caption',self.request.files[0].metadata_json)
        self.assertEqual(writer.transfer().code,'fixture_transfer_already_attempted')
        self.assertEqual(self.driver.sends,2)
    def test_request_is_immutable(self):
        with self.assertRaises(AttributeError):self.request.files=()
        with self.assertRaises(AttributeError):self.request.files[0].kind='floorplan'
    def test_unclassified_or_unbound_import_rejected(self):
        f=self.request.files[0]
        for kind in (None,'unknown','image'):
            with self.assertRaises(TransferRejected):replace(f,kind=kind)
        with self.assertRaises(TransferRejected):replace(self.request,files=(replace(f,source_evidence_json='{}'),)+self.request.files[1:])
        with self.assertRaises(TransferRejected):replace(self.request,files=(replace(f,kind='floorplan'),)+self.request.files[1:])
        self.assertEqual(self.driver.sends,0)
    def test_duplicate_hash_and_noncontiguous_order_rejected(self):
        with self.assertRaises(TransferRejected):replace(self.request,files=(self.request.files[0],self.request.files[0]))
        with self.assertRaises(TransferRejected):replace(self.request,files=tuple(reversed(self.request.files)))
    def test_total_bytes_and_metadata_are_bounded_before_access(self):
        with self.assertRaisesRegex(TransferRejected,'metadata_limit'):
            replace(self.request.files[0],metadata_json='x'*8193)
        items=[]
        for i in range(5):
            f=replace(self.request.files[0],media_id=f'limit-{i}',position=i,size=32*1024*1024,
                      sha256=str(i)*64,local_path=str(Path(self.tmp.name)/f'limit-{i}.png'))
            items.append(f)
        with self.assertRaisesRegex(TransferRejected,'request_limit'):replace(self.request,files=tuple(items))
        self.assertEqual(self.driver.sends,0)
    def test_nonempty_baseline_rejected(self):
        with self.assertRaises(TransferRejected):replace(self.request,baseline_json='{}')
    def test_nonlocal_origin_refused_before_dom_or_files(self):
        self.driver.current_url='https://hub.example/draft/222222/media'
        with patch.object(self.driver,'execute_script',side_effect=AssertionError):
            with self.assertRaisesRegex(TransferRejected,'fixture_origin'):self.bind()
    def test_nonce_consumed_on_failed_bind(self):
        self.driver.state['context']='{}'
        with self.assertRaises(TransferRejected):self.bind()
        with self.assertRaisesRegex(TransferRejected,'already_used'):self.bind()
    def test_invalid_shape_blocks(self):
        for key in ('photo','floorplan'):
            with self.subTest(key=key):
                self.nonce=secrets.token_hex(32);self.driver=Driver(self.request,self.nonce)
                self.driver.state['controls'][key]['disabled']=True
                with self.assertRaises(TransferRejected):self.bind()
                self.assertEqual(self.driver.sends,0)
    def test_changed_file_blocks_whole_batch(self):
        writer=self.bind();Path(self.request.files[-1].local_path).write_bytes(b'changed')
        report=writer.transfer();self.assertEqual(report.code,'local_file_changed');self.assertEqual(self.driver.sends,0)
    def test_same_size_changed_hash_blocks(self):
        writer=self.bind();f=self.request.files[0];Path(f.local_path).write_bytes(b'x'*f.size)
        self.assertEqual(writer.transfer().code,'local_file_changed');self.assertEqual(self.driver.sends,0)
    def test_path_and_handle_ctimes_may_differ_but_must_each_stay_stable(self):
        item=self.request.files[0]
        original_stat,original_fstat=Path.stat,os.fstat
        for mutation in (None,'path_ctime','handle_ctime','path_mtime','handle_mtime','path_identity','handle_birthtime'):
            with self.subTest(mutation=mutation):
                calls={'path':0,'handle':0}
                def copied(details,api):
                    calls[api]+=1
                    values={key:getattr(details,key) for key in ('st_mode','st_dev','st_ino','st_size','st_mtime_ns')}
                    values['st_ctime_ns']=100 if api=='path' else 200
                    values['st_birthtime_ns']=50
                    if calls[api]==2:
                        if mutation==api+'_ctime':values['st_ctime_ns']+=1
                        if mutation==api+'_mtime':values['st_mtime_ns']+=1
                        if mutation==api+'_identity':values['st_ino']+=1
                        if mutation==api+'_birthtime':values['st_birthtime_ns']+=1
                    return SimpleNamespace(**values)
                def path_stat(path,*args,**kwargs):
                    details=original_stat(path,*args,**kwargs)
                    # lstat's follow_symlinks=False checks stay real/unmodified.
                    return copied(details,'path') if kwargs.get('follow_symlinks',True) and str(path)==item.local_path else details
                def handle_stat(fd):return copied(original_fstat(fd),'handle')
                with patch.object(Path,'stat',path_stat),patch('quick_relist.hub_media_writer.os.fstat',handle_stat):
                    if mutation is None:
                        self.assertEqual(_local_file(item),(original_stat(Path(item.local_path)).st_dev,original_stat(Path(item.local_path)).st_ino))
                    else:
                        with self.assertRaisesRegex(TransferRejected,'local_file_changed'):_local_file(item)
                self.assertEqual(calls,{'path':2,'handle':2})
    def test_symlink_rejected(self):
        link=Path(self.tmp.name)/'link.png'
        try:link.symlink_to(self.request.files[0].local_path)
        except OSError:self.skipTest('Symlink unavailable')
        with self.assertRaisesRegex(TransferRejected,'local_file_unsafe'):_local_file(replace(self.request.files[0],local_path=str(link)))
    def test_context_window_and_nodes_changed_before_selection(self):
        for mutation in ('context','window','nodes','baseline'):
            with self.subTest(mutation=mutation):
                self.nonce=secrets.token_hex(32);self.driver=Driver(self.request,self.nonce);writer=self.bind()
                if mutation=='context':self.driver.state['context']='{}'
                if mutation=='window':self.driver.current_window_handle='new'
                if mutation=='nodes':self.driver.state['elements']['photo']=Input(self.driver,'photo')
                if mutation=='baseline':self.driver.state['files']['photo']=[{}]
                self.assertEqual(writer.transfer().status,'blocked');self.assertEqual(self.driver.sends,0)
    def test_exception_after_selection_readonly_recovery_no_retry(self):
        writer=self.bind();self.driver.throw=True
        report=writer.transfer();self.assertEqual(report.status,'partial_or_uncertain');self.assertTrue(report.upload_state_uncertain)
        self.assertNotIn('PRIVATE',str(report));self.assertNotIn(self.tmp.name,str(report))
        self.assertEqual(len(writer.observe().modeled_receipt_ids),2)
        writer.transfer();writer.observe();self.assertEqual(self.driver.sends,1)
    def test_interruption_is_uncertain(self):
        writer=self.bind();self.driver.interrupt=True
        self.assertEqual(writer.transfer().code,'interrupted');self.assertTrue(writer.observe().upload_state_uncertain)
    def test_pending_then_receipts_recovery(self):
        writer=self.bind();self.driver.pending=True
        self.assertEqual(writer.transfer().code,'modeled_receipts_pending')
        for f in self.request.files:self.driver.add_receipt(f)
        self.assertEqual(writer.observe().status,'synthetic_receipts_observed');self.assertEqual(self.driver.sends,2)
    def test_thumbnail_without_receipt_is_not_success(self):
        writer=self.bind();writer.transfer();self.driver.state['receipts']=[]
        self.assertEqual(writer.observe().code,'shared_grid_mismatch')
    def test_wrong_and_duplicate_receipt_rejected(self):
        writer=self.bind();writer.transfer()
        self.driver.state['receipts'][0]['sha256']='0'*64
        self.assertEqual(writer.observe().code,'modeled_receipt_mismatch')
    def test_actual_selected_hash_required(self):
        writer=self.bind();writer.transfer();self.driver.hashes['photo'][0]['sha256']='0'*64
        self.assertEqual(writer.observe().code,'selected_bytes_mismatch')
    def test_boolean_receipt_position_is_not_an_integer(self):
        writer=self.bind();writer.transfer()
        for index,value in ((0,False),(2,True)):
            previous=self.driver.state['receipts'][index]['source_position']
            self.driver.state['receipts'][index]['source_position']=value
            self.assertEqual(writer.observe().code,'modeled_receipt_mismatch')
            self.driver.state['receipts'][index]['source_position']=previous
    def test_boolean_control_flags_are_not_numeric(self):
        self.driver.state['controls']['photo']['multiple']=1
        with self.assertRaisesRegex(TransferRejected,'media_structure_rejected'):self.bind()
    def test_unexpected_metadata_rejected_before_any_async_byte_read(self):
        writer=self.bind();writer.transfer()
        for key,value in (('name','unexpected.png'),('size',2**40),('size',True),('type','application/octet-stream'),('lastModified',True)):
            with self.subTest(key=key,value=value):
                # Wrong metadata in the second kind must block hashing of both.
                item=self.driver.state['files']['floorplan'][0];previous=item[key];item[key]=value
                self.driver.async_reads=0
                self.assertEqual(writer.observe().code,'selected_metadata_mismatch')
                self.assertEqual(self.driver.async_reads,0)
                item[key]=previous
    def test_context_change_before_hash_blocks_async_read(self):
        writer=self.bind();writer.transfer();state=writer._read()
        self.driver.state['context']='{}';self.driver.async_reads=0
        with self.assertRaisesRegex(TransferRejected,'fixture_identity_changed'):writer._verify_selected(state)
        self.assertEqual(self.driver.async_reads,0)
    def test_boolean_hashed_metadata_is_rejected(self):
        writer=self.bind();writer.transfer();self.driver.hashes['photo'][0]['lastModified']=True
        self.assertEqual(writer.observe().code,'selected_bytes_mismatch')
    def test_readonly_script_does_not_inject_events_or_state(self):
        for code in (MEDIA_STATE_SCRIPT,FILE_BYTES_SCRIPT):
            for forbidden in ('dispatchEvent','fetch(','.click(','.files =','localStorage','sessionStorage','document.cookie'):
                self.assertNotIn(forbidden,code)


if __name__=='__main__':unittest.main()
