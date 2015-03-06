#!/usr/bin/python

## Copyright (C) Amnesia <amnesia at boum dot org>
## Copyright (C) 2014 troubadour <trobador@riseup.net>
## Copyright (C) 2014 Patrick Schleizer <adrelanos@riseup.net>
## See the file COPYING for copying conditions.

# This filter proxy should allow Torbutton to request a
# new Tor circuit, without exposing dangerous control requests
# like "GETINFO address" to applications running as a local user.

# If something goes wrong, an error code is returned, and
# Torbutton will display a warning dialog that New Identity failed.

# Only one application can talk through this filter proxy
# simultaneously. A malicious application that is running as a
# local user could use this to prevent other applications from
# doing NEWNYM. But it could just as well rewrite the
# TOR_CONTROL_PORT environment variable to itself or do something else.

from gevent import socket
from gevent.server import StreamServer
import binascii
import os
import glob
import logging
import signal
import sys



def signal_sigterm_handler(signal, frame):
  logger.info('Signal sigterm received. Exiting.')
  sys.exit(143)

def signal_sigint_handler(signal, frame):
  logger.info('Signal sigint received. Exiting.')
  sys.exit(130)



class UnexpectedAnswer(Exception):

  def __init__(self, msg):
    self.msg = msg

  def __str__(self):
    return "[UnexpectedAnswer] " + self.msg



#class TCPHandler(SocketServer.StreamRequestHandler):

def do_request_real(request):
  # check if tor socket exists
  if not os.path.exists(SOCKET):
    logger.critical('Tor socket: "%s" does not exist' % (SOCKET))
    return

  # The "lie" implemented in cpfp-bash
  if request == 'getinfo net/listeners/socks' and LIMIT_GETINFO_NET_LISTENERS_SOCKS:
    temp = '250-net/listeners/socks="127.0.0.1:9150"\n'
    logger.info('Lying: %s' % (temp.strip()))
    return(temp)

  # Read authentication cookie
  with open(AUTH_COOKIE, "rb") as f:
    rawcookie = f.read(32)
    hexcookie = binascii.hexlify(rawcookie)

    # Connect to the real control port
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    sock.connect(SOCKET)
    readh = sock.makefile("r")
    writeh = sock.makefile("w")

    # Authenticate
    writeh.write("AUTHENTICATE " + hexcookie + "\n")
    writeh.flush()
    answer = readh.readline()
    if not answer.strip() == "250 OK":
      raise UnexpectedAnswer("AUTHENTICATE failed")

    # Send the request
    writeh.write(request + '\n')
    writeh.flush()
    answer = sock.recv(LIMIT_STRING_LENGTH)

    sock.close()
    return answer


def do_request(request):
  logger.info('Request: %s' % (request.strip()))
  # Catch innocent exceptions, will report error instead
  try:
    answer = do_request_real(request)
    logger.info('Answer: %s' % (answer.strip()))
    return answer
  except (IOError, UnexpectedAnswer) as e:
    logger.error(e)


def handle(sock, address):
  # Keep accepting requests
  fh = sock.makefile()
  while True:
    # Read in a newline terminated line
    line = fh.readline()
    if not line:
      break
    # Strip escaped chars and white spaces at beginning and end of string
    request = line.lower().strip()

    # Authentication request.
    if request.startswith("authenticate"):
      # Don't check authentication, since only
      # safe requests are allowed
      fh.write("250 OK\n")

    elif DISABLE_FILTERING:
      # Pass all requests
      answer = do_request(request)
      fh.write(answer)

    elif request in WHITELIST:
      # Filtering enabled
      answer = do_request(request)
      fh.write(answer)

    else:
      # Everything else we ignore/block
      fh.write("510 Request filtered\n")
      logger.info('Request: %s' % (request.strip()))
      logger.warning('Answer: 510 Request filtered "%s"' % (request))

    # Ensure the answer was written
    fh.flush()

  # Ensure all data was written
  fh.flush()

if __name__ == "__main__":
  # Generate random user ID.
  pid = os.getpid()
  #print pid

  # Create logger
  logging.basicConfig(filename='/var/log/control-port-filter-python.log', level=logging.NOTSET)
  logger = logging.getLogger(unicode(pid))

  signal.signal(signal.SIGTERM, signal_sigterm_handler)
  signal.signal(signal.SIGINT, signal_sigint_handler)

  # Default control port filer configuration
  IP = '10.152.152.10'
  PORT =  9052
  SOCKET = '/var/run/tor/control'
  AUTH_COOKIE = '/var/run/tor/control.authcookie'
  DISABLE_FILTERING = False
  LIMIT_STRING_LENGTH = 16384
  LIMIT_GETINFO_NET_LISTENERS_SOCKS = True
  WHITELIST = ['signal newnym', 'getinfo net/listeners/socks', 'getinfo status/bootstrap-phase', \
               'getinfo status/circuit-established', 'quit']

  # Read and override configuration from files
  if os.path.exists('/etc/cpfpy.d/'):
    files = sorted(glob.glob('/etc/cpfpy.d/*'))

    if  files:
      RequestList = ''
      for conf in files:
        if not conf.endswith('~') and conf.count('.dpkg-') == 0:
          logger.info('Configuration read from "%s"' % (conf))
          with open(conf) as f:
            for line in f:
              if line.startswith('CONTROL_PORT_FILTER_DISABLE_FILTERING'):
                k, value = line.split('=')
                DISABLE_FILTERING = value.strip() == 'true'
              if line.startswith('CONTROL_PORT_FILTER_LIMIT_STRING_LENGTH'):
                k, value = line.split('=')
                LIMIT_STRING_LENGTH = int(value.strip())
              if line.startswith('CONTROL_PORT_FILTER_LIMIT_GETINFO_NET_LISTENERS_SOCKS'):
                k, value = line.split('=')
                LIMIT_GETINFO_NET_LISTENERS_SOCKS = value.strip() == 'true'
              if line.startswith('CONTROL_PORT_FILTER_WHITELIST'):
                k, value = line.split('=')
                # concatenate values from files, add a comma
                RequestList = RequestList + value.strip() + ','
              if line.startswith('CONTROL_PORT_FILTER_PORT'):
                k, value = line.split('=')
                PORT = int(value.strip())
              if line.startswith('CONTROL_PORT_FILTER_IP'):
                k, value = line.split('=')
                IP = str(value.strip())
              if line.startswith('CONTROL_PORT_SOCKET'):
                k, value = line.split('=')
                SOCKET = str(value.strip())
              if line.startswith('CONTROL_PORT_AUTH_COOKIE'):
                k, value = line.split('=')
                AUTH_COOKIE = str(value.strip())

      # Disable limit.
      if LIMIT_STRING_LENGTH == -1:
        # "sock.recv()" requires an argument. 64 KB, arbitrary.
        LIMIT_STRING_LENGTH = 65536

      WHITELIST = RequestList.split(',')
      # Remove last element (comma)
      WHITELIST.pop()
      # Remove duplicates
      WHITELIST = list(set(WHITELIST))

    else:
      logger.warning('No file found in user configuration folder "/etc/cpfpy.d".')
      logger.warning('Running with default configuration.')

  else:
    logger.warning('User configuration folder "/etc/cpfpy.d" does not exist.')
    logger.warning('Running with default configuration.')

  # Catch server exceptions.
  # Most likely one: "Address already in use" if control port filter running.
  try:
    # Starts a TCP server
    #   Logger available levels:
    #    .info
    #    .warning
    #    .error
    #    .critical
    #    .debug
    logger.info("Trying to start Tor control port filter on IP %s port %s" % (IP, PORT))
    # ACCEPT CONCURRENT CONNECTIONS.
    # limit to 5 simultaneous connections.
    server = StreamServer((IP, PORT), handle, spawn=5)

    #print "Tor control port filter started, listening on IP %s port %s" % (IP, PORT)
    logger.info("Tor control port filter started, listening on IP %s port %s" % (IP, PORT))
    server.serve_forever()

  except IOError as e:
    logger.critical('Server error %s' % (e))
    logger.critical('Exiting.')
    sys.exit(1)
