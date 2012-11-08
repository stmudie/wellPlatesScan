from gevent import monkey; monkey.patch_all()
import gevent
from socketio import socketio_manage
from socketio.server import SocketIOServer
from socketio.namespace import BaseNamespace
import cPickle as pickle
import qrcode
from StringIO import StringIO
from epics import PV, caput

from flask import Flask, request, send_file, render_template
import redis

from jinja2 import Environment, FileSystemLoader

env = Environment(loader=FileSystemLoader('./templates'))

wellIDs = ['Well_' + str(num) for num in range(96)]

r = redis.StrictRedis(host='localhost', port=6379, db=0)

app = Flask(__name__)

app.debug = True

attributes = { 'epn': ['mudie_123'] }

def serve_pil_image(pil_img):
    img_io = StringIO()
    pil_img.save(img_io)
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')

class WellNamespace(BaseNamespace):
    def on_connect(self):
        print 'connect'
	self.emit('epn', self.request['epn'][0])
	plates = list(r.smembers('well:' + self.request['epn'][0] + ':plates'))
	self.emit('loadlist',plates)

    def on_save(self, data):
        epn = self.request['epn'][0]
        r.sadd('well:epn', epn)
        r.sadd('well:' + epn + ':plates', data['platename'])
        r.set('well:' + epn + ':plate:' + data['platename'], pickle.dumps(data))
 
    def on_load(self, epn, plate):
	data = pickle.loads(r.get('well:' + epn + ':plate:' + plate))
	self.emit('platedata', epn, plate, data)

    def run(self,epn,plate,type='all'):
        data = pickle.loads(r.get('well:' + epn + ':plate:' + plate))
	if type == 'all':
	    sampleNames = [data['sampleNames'][int(order)] for order in data['sampleOrder'] if data['sampleNames'][int(order)] != ""]
	    positions = [1+int(order) for order in data['sampleOrder'] if data['sampleNames'][int(order)] != ""]
	    types = [int(data['sampleType'][int(order)]) for order in data['sampleOrder'] if data['sampleNames'][int(order)] != ""]
	    washes = [int(data['washType'][int(order)]) for order in data['sampleOrder'] if data['sampleNames'][int(order)] != ""]
	    
	elif type == 'selected':
	    sampleNames = [data['sampleNames'][int(order)] for order in data['sampleOrder'] if data['sampleInclude'][int(order)] == 1 and data['sampleNames'][int(order)] != ""]
	    positions = [1+int(order) for order in data['sampleOrder'] if data['sampleInclude'][int(order)] == 1 and data['sampleNames'][int(order)] != ""]
	    types = [int(data['sampleType'][int(order)]) for order in data['sampleOrder'] if data['sampleInclude'][int(order)] == 1 and data['sampleNames'][int(order)] != ""]
	    washes = [int(data['washType'][int(order)]) for order in data['sampleOrder'] if data['sampleInclude'][int(order)] == 1 and data['sampleNames'][int(order)] != ""]
	
	sampleNameString = "".join(sampleNames)
	sampleNameLen = [len(name) for name in sampleNames]
        sampleNameCoord = []
	sampleNameCoord.append(0) 
        for i in range(1,len(sampleNameLen)):
            sampleNameCoord.append(sampleNameLen[i-1]+sampleNameCoord[i-1])

        print len(sampleNameCoord)

	basePV = "SR13ID01HU02IOC02:"

	# Setup global scan record parameters
	scanPV = basePV + 'scan1.'
	result = 0
	result += caput(basePV + 'fileIndex1',1)
	result += caput(scanPV+'CMND',6)
	result += caput(scanPV+'BSPV','SR13ID01SYR01:SCAN_RECORD_MESSAGE.VAL')
	result += caput(scanPV+'BSCD',0)
	result += caput(scanPV+'ASPV','SR13ID01SYR01:SCAN_RECORD_MESSAGE.VAL')
	result += caput(scanPV+'ASCD',1)
	result += caput(scanPV+'D01PV','SR13ID01SYR01:FULL_SEL_SQ.VAL')
	result += caput(scanPV+'PDLY',2)
	result += caput(scanPV+'DDLY',5)
	if result != 9 :
	    print "Something wrong setting " + str(9-result) + " PVs. Continuing anyway."
	
	# Setup positioners for proteins
	result = 0
	positioner = ['SR13ID01SYR01:SMPL_RAW_COORD','SR13ID01SYR01:WASH_TYPE','SR13ID01HU02IOC04:SMPL_TYPE']
	dictKey = ['COORD','WASH','TYPE']
	data = {'COORD': positions, 'WASH': washes, 'TYPE': types}
	for posNum in range(3):
	    scanPV = basePV + 'scan1.'
            result += caput(scanPV+'R'+str(1+posNum)+'PV', positioner[posNum])
            result += caput(scanPV+'P'+str(1+posNum)+'PV', positioner[posNum])
	    result += caput(scanPV+'P'+str(1+posNum)+'SM', 1)
            result += caput(scanPV+'P'+str(1+posNum)+'PA', data[dictKey[posNum]])
        result += caput(scanPV+'NPTS', len(positions))
	if result != 13 :
	    print "Something wrong setting " + str(13-result) + " some PVs. Continuing anyway."
	
	# Setup sample name positioners
        result = 0
        result += caput(basePV+'fileNames', str(sampleNameString))
	result += caput(basePV+'fileIndices', sampleNameCoord)
	result += caput(scanPV+'P4SM', 0)
	result += caput(scanPV+'P4SP', 1)
	result += caput(scanPV+'P4EP', len(positions))
	result += caput(scanPV+'R4PV', basePV + 'fileIndex1')
	result += caput(scanPV+'P4PV', basePV + 'fileIndex1')
	if result != 7 :
	    print "Something wrong setting some PVs. Continuing anyway."
	    
	# Setup detectors
	result = caput(scanPV+'T1PV', 'SR13ID01SYR01:FULL_SEL_SQ.VAL')
	if result != 1 :
	    print "Something wrong setting some PVs. Continuing anyway."
	
	scanning = caput(basePV+'scan1.EXSC', 1)

    def on_runall(self,epn,plate):
	self.run(epn, plate, type = 'all')

    def on_runselected(self,epn,plate):
	self.run(epn, plate, type = 'selected')

    def recv_message(self, message):
        print "PING!!!", message

@app.route("/socket.io/<path:path>")
def run_socketio(path):
    print path
    socketio_manage(request.environ, {'': WellNamespace}, attributes)
    return ''

@app.route("/")
def well():
    template = env.get_template('base.html')
    return template.render(wells=wellIDs,epn='mudie_123',plate='')

@app.route("/<epn>/<plate>")
def well1(epn,plate):
    template = env.get_template('base.html')
    return template.render(wells=wellIDs,epn=epn,plate=plate,rel='../')

@app.route("/qrcode/<epn>/<plate>")
def serve_img(epn,plate):
    print 'qrcode1'
    img = qrcode.make('http://10.6.0.65/' + epn + '/' + plate)
    return serve_pil_image(img)

@app.route("/qrcode")
def serve_img_empty():
    print 'qrcode'
    img = qrcode.make('This is not the code you are looking for.')
    return serve_pil_image(img)

if __name__ == '__main__':
    print 'Listening on port 8081 and on port 843 (flash policy server)'
    SocketIOServer(('0.0.0.0', 8081), app,
        resource="socket.io", policy_server=True,
        policy_listener=('0.0.0.0', 10843)).serve_forever()
