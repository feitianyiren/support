import os
import copy
import functools
import weakref
import time

from asf.asf_context import ASFError
#TODO: migrate ASFError out of ASF to a more root location
import gevent.pool
import gevent.socket
import gevent.threadpool

import pp_crypt

CPU_THREAD = None # Lazily initialize -- mhashemi 6/11/2012
CPU_THREAD_ENABLED = True
GREENLET_ANCESTORS = weakref.WeakKeyDictionary()
GREENLET_CORRELATION_IDs = weakref.WeakKeyDictionary()

@functools.wraps(gevent.spawn)
def spawn(*a, **kw):
    gr = gevent.spawn(*a, **kw)
    GREENLET_ANCESTORS[gr] = gevent.getcurrent()
    return gr

def get_cur_correlation_id():
    cur = gevent.getcurrent()
    #walk ancestors looking for a correlation id
    while cur not in GREENLET_CORRELATION_IDs and cur in GREENLET_ANCESTORS:
        cur = GREENLET_ANCESTORS[cur]
    #if no correlation id found, create a new one at highest level
    if cur not in GREENLET_CORRELATION_IDs:
        #this is reproducing CalUtility.cpp
        #TODO: where do different length correlation ids come from in CAL logs?
        t = time.time()
        corr_val = "{0}{1}{2}{3}".format(gevent.socket.gethostname(), 
            os.getpid(), int(t), int(t%1 *10**6))
        corr_id = "{0:x}{1:x}".format(pp_crypt.fnv_hash(corr_val), int(t%1 * 10**6))
        GREENLET_CORRELATION_IDs[cur] = corr_id
    return GREENLET_CORRELATION_IDs[cur]

def set_cur_correlation_id(corr_id):
    GREENLET_CORRELATION_IDs[gevent.getcurrent()] = corr_id

def cpu_bound(f):
    '''
    decorator to mark a function as cpu-heavy; will be executed in a separate
    thread to avoid blocking any socket communication
    '''
    @functools.wraps(f)
    def g(*a, **kw):
        if not CPU_THREAD_ENABLED:
            return f(*a, **kw)
        global CPU_THREAD
        if CPU_THREAD is None:
            CPU_THREAD = gevent.threadpool.ThreadPool(1)
        return CPU_THREAD.apply_e((Exception,), f, a, kw)
    g.no_defer = f
    return g

def close_threadpool():
    global CPU_THREAD
    if CPU_THREAD:
        CPU_THREAD.join()
        CPU_THREAD.kill()
        CPU_THREAD = None
    return

def _safe_req(req):
    'capture the stack trace of exceptions that happen inside a greenlet'
    try:
        return req()
    except Exception as e:
        raise ASFError(e)

def join(asf_reqs, raise_exc=False, timeout=None):
    greenlets = [spawn(_safe_req, req) for req in asf_reqs]
    gevent.joinall(greenlets, raise_error=raise_exc, timeout=timeout)
    results = []
    for gr, req in zip(greenlets, asf_reqs):
        if gr.successful():
            results.append(gr.value)
        elif gr.ready(): #finished, but must have had error
            results.append(gr.exception)
        else: #didnt finish, must have timed out
            results.append(ASFTimeoutError(req, timeout))
    return results

class ASFTimeoutError(ASFError):
    def __init__(self, request=None, timeout=None):
        try:
            self.ip = request.ip
            self.port = request.port
            self.service_name = request.service
            self.op_name = request.operation
        except AttributeError as ae:
            pass
        if timeout:
            self.timeout = timeout

    def __str__(self):
        ret = "ASFTimeoutError"
        try:
            ret += " encountered while to trying execute "+self.op_name \
                   +" on "+self.service_name+" ("+str(self.ip)+':'      \
                   +str(self.port)+")"
        except AttributeError:
            pass
        try:
            ret += " after "+str(self.timeout)+" seconds"
        except AttributeError:
            pass
        return ret

### What follows is code related to map() contributed from MoneyAdmin's asf_util
class Node(object):
    def __init__(self, ip, port, **kw):
        self.ip   = ip
        self.port = port
        
        # in case you want to add name/location/id/other metadata
        for k,v in kw.items():
            setattr(self, k, v)

# call it asf_map to avoid name collision with builtin map?
# return callable to avoid collision with kwargs?
def map_factory(op, node_list, raise_exc=False, timeout=None):
    """
    map_factory() enables easier concurrent calling across multiple servers,
    provided a node_list, which is an iterable of Node objects.
    """
    def asf_map(*a, **kw):
        return join([op_ip_port(op, node.ip, node.port).async(*a, **kw)
                        for node in node_list], raise_exc=raise_exc, timeout=timeout)
    return asf_map

def op_ip_port(op, ip, port):
    serv = copy.copy(op.service)
    serv.meta = copy.copy(serv.meta)
    serv.meta.ip = ip
    serv.meta.port = port
    op = copy.copy(op)
    op.service = serv
    return op


#these words fit the following criteria:
#1- one or two syllables (fast to say), short (fast to write)
#2- simple to pronounce and spell (no knee/knife)
#3- do not have any homonyms (not aunt/ant, eye/I, break/brake, etc)
# they are used to generate easy to communicate correlation IDs
# should be edited if areas of confusion are discovered :-)
SIMPLE_WORDS_LIST = ["air", "art", "arm", "bag", "ball", "bank", "bath", "back",
    "base", "bed", "beer", "bell", "bird", "block", "blood", "boat", "bone", "bowl", 
    "box", "boy", "branch", "bridge", "bus", "cake", "can", "cap", "car", 
    "case", "cat", "chair", "cheese", "child", "city", "class", "clock", "cloth",
    "cloud", "coat", "coin", "corn", "cup", "day", "desk", "dish", "dog", "door",
    "dream", "dress", "drink", "duck", "dust", "ear", "earth", "egg", "face", "fact",
    "farm", "fat", "film", "fire", "fish", "flag", "food", "foot", "fork", "game",
    "gate", "gift", "glass", "goat", "gold", "grass", "group", "gun", "hair",
    "hand", "hat", "head", "heart", "hill", "home", "horse", "house", "ice",
    "iron", "job", "juice", "key", "king", "lamp", "land", "leaf", "leg", "life",
    "lip", "list", "lock", "luck", "man", "map", "mark", "meal", "meat", "milk",
    "mind", "mix", "month", "moon", "mouth", "name", "net", "noise", "nose",
    "oil", "page", "paint", "pan", "park", "party", "pay", "path", "pen",
    "pick", "pig", "pin", "plant", "plate", "point", "pool", "press", "prize",
    "salt", "sand", "seat", "ship", "soup", "space", "spoon", "sport", 
    "spring", "shop", "show", "sink", "skin", "sky", "smoke", "snow", "step", "stone",
    "store", "star", "street", "sweet", "swim", "tea", "team", "test", "thing", 
    "tool", "tooth", "top", "town", "train", "tram", "tree", "type",
    "wash", "west", "wife", "wind", "wire", "word", "work", "world", "yard", "zoo"]