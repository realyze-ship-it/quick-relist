"""Generated synthetic PNGs and a nonce-bound server that hashes received bytes."""
from contextlib import contextmanager
from dataclasses import asdict
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import secrets
import struct
from threading import Thread, Lock
import time
import zlib

from quick_relist.hub_form_writer import TransferContext, _json, _digest
from quick_relist.hub_media_writer import MediaFile, MediaTransferRequest, empty_baseline


def png(red):
    def chunk(kind,data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(bytes((0,red,0,0))))+chunk(b'IEND',b'')


def fixture_request(directory):
    context=TransferContext('synthetic-operation','synthetic-account','synthetic-agency','111111','RXCU111111',
                            'source-rev','a'*64,'b'*64,'222222','draft-rev','media')
    files=[]
    for i,kind in enumerate(('photo','floorplan','photo')):
        data=png(30+i);path=Path(directory).resolve()/f'synthetic-{i}.png';path.write_bytes(data)
        digest=hashlib.sha256(data).hexdigest();asset_id=f'asset-{i}'
        evidence={'asset_id':asset_id,'source_id':context.source_id,'source_revision':context.expected_source_revision,
                  'source_capture_sha256':context.source_capture_sha256,'kind':kind,'position':i,'sha256':digest}
        files.append(MediaFile(asset_id,kind,i,len(data),digest,str(path),_json(evidence),
                               _json({'caption':'Synthetic caption','cover':i==0,'rotation':0})))
    return MediaTransferRequest(context,'synthetic-parent','c'*64,tuple(files),_json(empty_baseline()))


def fixture_document(request,nonce,scenario='normal'):
    template=(Path(__file__).parent/'fixtures'/'hub_media_transfer.html').read_text()
    values={'NONCE':nonce,'CONTEXT':escape(_json(asdict(request.context)),quote=True),
            'REQUEST':request.fingerprint,'CONFIG':_json({'scenario':scenario})}
    for key,value in values.items():template=template.replace('@@'+key+'@@',value)
    return template


@contextmanager
def serve(request,scenario='normal',delay=0):
    nonce=secrets.token_hex(32)
    path=f'/quick-relist-fixture/{nonce}/draft/{request.context.draft_id}/media'
    data=fixture_document(request,nonce,scenario).encode()
    ledger=[];lock=Lock()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!=path:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)
        def do_POST(self):
            kind=self.path.removeprefix(path+'/upload/')
            if self.path!=path+'/upload/'+kind or kind not in ('photo','floorplan'):
                self.send_error(404);return
            try:size=int(self.headers.get('Content-Length','0'))
            except ValueError:self.send_error(400);return
            if not 0<size<=32*1024*1024:self.send_error(400);return
            content=self.rfile.read(size);digest=hashlib.sha256(content).hexdigest()
            matches=[f for f in request.files if f.kind==kind and f.size==len(content) and f.sha256==digest]
            if len(matches)!=1:self.send_error(409);return
            f=matches[0]
            with lock:
                if any(r['media_id']==f.media_id for r in ledger):self.send_error(409);return
                receipt={'media_id':f.media_id,'upload_id':'synthetic-upload-'+f.media_id,'sha256':digest,
                         'size':len(content),'kind':kind,'source_position':f.position,'request':request.fingerprint}
                ledger.append(receipt)
            if delay:time.sleep(delay)
            body=_json(receipt).encode();self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    try:yield f'http://127.0.0.1:{server.server_port}{path}',nonce,ledger
    finally:server.shutdown();server.server_close();worker.join(timeout=5)
