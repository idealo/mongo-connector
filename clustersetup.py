"""
(C) Copyright 2012, 10gen
"""

import subprocess
import sys
import time
from pymongo import Connection
from pymongo.errors import ConnectionFailure
from os import path
from mongo_internal import Daemon
from threading import Timer
from oplog_manager import OplogThread
from solr_doc_manager import SolrDocManager
from pysolr import Solr
from util import long_to_bson_ts

# Global path variables

SETUP_DIR = path.expanduser("~/mongo-connector/test")
DEMO_SERVER_DATA = SETUP_DIR + "/data"
DEMO_SERVER_LOG = SETUP_DIR + "/logs"
MONGOD_KSTR = " --dbpath " + DEMO_SERVER_DATA
MONGOS_KSTR = "mongos --port 27220"
MONGOD_PORTS = ["27117", "27118", "27119", "27218",
                 "27219", "27220", "27017"]

def remove_dir(path):
    """Remove supplied directory"""
    command = ["rm", "-rf", path]
    subprocess.Popen(command).communicate()

remove_dir(DEMO_SERVER_LOG)
remove_dir(DEMO_SERVER_DATA)


def create_dir(path):
    """Create supplied directory"""
    command = ["mkdir", "-p", path]
    subprocess.Popen(command).communicate()

create_dir(DEMO_SERVER_DATA + "/standalone/journal")
create_dir(DEMO_SERVER_DATA + "/replset1a/journal")
create_dir(DEMO_SERVER_DATA + "/replset1b/journal")
create_dir(DEMO_SERVER_DATA + "/replset1c/journal")
create_dir(DEMO_SERVER_DATA + "/shard1a/journal")
create_dir(DEMO_SERVER_DATA + "/shard1b/journal")
create_dir(DEMO_SERVER_DATA + "/config1/journal")
create_dir(DEMO_SERVER_LOG)


def killMongoProc(port):
    cmd = ["pgrep -f \"" + str(port) + MONGOD_KSTR + "\" | xargs kill -9"]
    executeCommand(cmd)
    
def killMongosProc():
    cmd = ["pgrep -f \"" + MONGOS_KSTR + "\" | xargs kill -9"]
    executeCommand(cmd)

def killAllMongoProc():
    """Kill any existing mongods"""
    for port in MONGOD_PORTS:
        killMongoProc(port)

def startMongoProc(port, name, data, log):
    #Create the replica set
    CMD = ["mongod --fork --replSet " + name + " --noprealloc --port " + port + " --dbpath "
    + DEMO_SERVER_DATA + data + " --shardsvr --rest --logpath "
    + DEMO_SERVER_LOG + log + " --logappend &"]
    executeCommand(CMD)
    checkStarted(int(port))


def executeCommand(command):
    """Wait a little and then execute shell command"""
    time.sleep(1)
    return subprocess.Popen(command, shell=True)


#========================================= #
#   Helper functions to make sure we move  #
#   on only when we're good and ready      #
#========================================= #


def tryConnection(port):
    """Uses pymongo to try to connect to mongod"""
    error = 0
    try:
        Connection('localhost', port)
    except Exception:
        error = 1
    return error


def checkStarted(port):
    """Checks if our the mongod has started"""
    connected = False

    while not connected:
        error = tryConnection(port)
        if error:
            #Check every 1 second
            time.sleep(1)
        else:
            connected = True


#================================= #
#       Run Mongo* processes       #
#================================= #

    
    

class ReplSetManager():

    def startCluster(self):
        # Kill all spawned mongods
        killAllMongoProc()

        # Kill all spawned mongos
        killMongosProc()

        # Create the replica set
        startMongoProc("27117", "demo-repl", "/replset1a", "/replset1a.log")
        startMongoProc("27118", "demo-repl", "/replset1b", "/replset1b.log")
        startMongoProc("27119", "demo-repl", "/replset1c", "/replset1c.log")
        
        # Setup config server
        CMD = ["mongod --oplogSize 500 --fork --configsvr --noprealloc --port 27220 --dbpath " +
        DEMO_SERVER_DATA + "/config1 --rest --logpath "
       + DEMO_SERVER_LOG + "/config1.log --logappend &"]
        executeCommand(CMD)
        checkStarted(27220)

# Setup the mongos
        CMD = ["mongos --port 27217 --fork --configdb localhost:27220 --chunkSize 1  --logpath " 
       + DEMO_SERVER_LOG + "/mongos1.log --logappend &"]
        executeCommand(CMD)
        checkStarted(27217)

# Configure the shards and begin load simulation
        #executeCommand(CMD).wait()
        cmd1 = "mongo --port 27117 " + SETUP_DIR + "/setup/configReplSet.js"
        cmd2 = "mongo --port 27217 " + SETUP_DIR + "/setup/configMongos.js"
        time.sleep(10)
        subprocess.call(cmd1, shell=True)
        time.sleep(20)
        subprocess.call(cmd2, shell=True)
        #time.sleep(10)
        #subprocess.call(cmd2, shell=True)    
    
    def abort_test(self):
        print 'test failed'
        sys.exit(1)
    
    def test_mongo_internal(self):
        t = Timer(60, self.abort_test)
        t.start()
        d = Daemon('localhost:27217', None)
        d.start()
        while len(d.shard_set) == 0:
            pass
        t.cancel()
                    
        d.stop()
        #the Daemon should recognize a single running shard
        assert len(d.shard_set) == 1
        #we want to add several shards
        
    def get_oplog_thread(self):
        #try sending invalid entry
        primary_conn = Connection('localhost', 27117)
        primary_conn['test']['test'].drop()
        mongos_conn = "localhost:27217"
        
        oplog_coll = primary_conn['local']['oplog.rs']
        oplog_coll.drop()           # reset the oplog
        
        primary_conn['local'].create_collection('oplog.rs', capped=True, size=10000)
        namespace_set = ['test.test']
        doc_manager = SolrDocManager('http://localhost:8080/solr')
        oplog = OplogThread(primary_conn, mongos_conn, oplog_coll, True, doc_manager, None, namespace_set)
        
        return (oplog, primary_conn, oplog_coll)
            
    def test_retrieve_doc(self):
        
        test_oplog, primary_conn, oplog_coll = self.get_oplog_thread()
        #testing for entry as none type
        entry = None
        assert (test_oplog.retrieve_doc(entry) is None)
        
        oplog_cursor = oplog_coll.find({},tailable=True, await_data=True)
        #oplog_cursor.next()  #skip first 'drop collection' operation
        
        
        primary_conn['test']['test'].insert ( {'name':'paulie'} )
        last_oplog_entry = oplog_cursor.next()
        target_entry = primary_conn['test']['test'].find_one()
            
        #testing for search after inserting a document
        assert (test_oplog.retrieve_doc(last_oplog_entry) == target_entry)
        
        primary_conn['test']['test'].update({'name':'paulie'}, {"$set": {'name':'paul'}} )
        
        last_oplog_entry = oplog_cursor.next()
        target_entry = primary_conn['test']['test'].find_one()        
        
        #testing for search after updating a document
        assert (test_oplog.retrieve_doc(last_oplog_entry) == target_entry)
        
        primary_conn['test']['test'].remove( {'name':'paul'} )
        last_oplog_entry = oplog_cursor.next()
        
        #testing for search after deleting a document
        assert (test_oplog.retrieve_doc(last_oplog_entry) == None)
        
        last_oplog_entry['o']['_id'] = 'badID'
        
        #testing for bad doc id as input
        assert (test_oplog.retrieve_doc(last_oplog_entry) == None)
        
        test_oplog.stop()
            
    def test_get_last_oplog_timestamp(self):
        
        #test empty oplog
        test_oplog, primary_conn, oplog_coll = self.get_oplog_thread()
        assert (test_oplog.get_last_oplog_timestamp() == None)
        
        #test non-empty oplog
        oplog_cursor = oplog_coll.find({},tailable=True, await_data=True)
        primary_conn['test']['test'].insert ( {'name':'paulie'} )
        last_oplog_entry = oplog_cursor.next()
        assert (test_oplog.get_last_oplog_timestamp() == last_oplog_entry['ts'])
        
        
    def test_dump_collection(self):
        
        test_oplog, primary_conn, oplog_coll = self.get_oplog_thread()
        solr_url = "http://localhost:8080/solr"
        solr = Solr(solr_url)
        solr.delete(q='*:*')
        
        #for empty oplog, no documents added
        assert (test_oplog.dump_collection(None) == None)
        assert (len(solr.search('*')) == 0)
        
        #with documents
        primary_conn['test']['test'].insert ( {'name':'paulie'} )
        search_ts = test_oplog.get_last_oplog_timestamp()
        test_oplog.dump_collection(search_ts)

        test_oplog.doc_manager.commit()
        solr_results = solr.search('*')
        assert (len(solr_results) == 1)
        solr_doc = solr_results.docs[0]
        assert (long_to_bson_ts(solr_doc['_ts']) == search_ts)
        assert (solr_doc['name'] == 'paulie')
        assert (solr_doc['ns'] == 'test.test')
        test_oplog.stop()
            
            
    


        
        
        
        

        
        
        #testing for valid entry
        """
            { "ts" : { "t" : 1341343596000, "i" : 1 }, "h" : NumberLong(0), "op" : "n", "ns" : "", "o" : { "msg" : "initiating set" } }
            { "ts" : { "t" : 1341347358000, "i" : 1 }, "h" : NumberLong("5704011229614309393"), "op" : "i", "ns" : "test.test", "o" : { "_id" : ObjectId("4ff3561efb3d8f91f511da5c"), "name" : "paulie" } }
            { "ts" : { "t" : 1341347475000, "i" : 1 }, "h" : NumberLong("-1269638662509784076"), "op" : "u", "ns" : "test.test", "o2" : { "_id" : ObjectId("4ff3561efb3d8f91f511da5c") }, "o" : { "$set" : { "name" : "paul" } } }
            { "ts" : { "t" : 1341347486000, "i" : 1 }, "h" : NumberLong("570562443735405485"), "op" : "d", "ns" : "test.test", "b" : true, "o" : { "_id" : ObjectId("4ff3561efb3d8f91f511da5c") } }
        """
        

"""
# Create the sharded cluster
CMD = ["mongod --oplogSize 500 --fork --shardsvr --noprealloc --port 27218 "
       "--dbpath "
       + DEMO_SERVER_DATA + "/shard1a --rest --logpath "
       + DEMO_SERVER_LOG + "/shard1a.log --logappend &"]
executeCommand(CMD)
checkStarted(27218)


CMD = ["mongod --oplogSize 500 --fork --shardsvr --noprealloc --port 27219 "
       "--dbpath "
       + DEMO_SERVER_DATA + "/shard1b --rest --logpath "
       + DEMO_SERVER_LOG + "/shard1b.log --logappend &"]
executeCommand(CMD)
checkStarted(27219)

"""




