#!/usr/bin/env python

from __future__ import division
import logging
import socket

# You need to be root to bind to port 23
port = 23
# Magic packet to which the controller will answer back
magic = "\xff\x04\x02\xfb"

def scan():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(('', port))  # bind before connect
    # TODO: check if it needs to be send over each interface separately
    s.sendto(magic, ('255.255.255.255', port))
    s.settimeout(1.0)  # 1s max to answer back

    try:
        while True:
            data, (addr, p) = s.recvfrom(1024)
            if not data:
                break
            elif data == magic:
                logging.debug("Skipping our own packet")
                continue
            elif data.startswith("\xfe") and len(data) > 25: # That should be an answer packet
                try:
                    # Look for the hostname (default is like "8742-15433\x00")
                    end_hn = data.index("\x00", 19)
                    hn = data[19:end_hn]
                    print "%s\t%s\t%d" % (hn, addr, p)
                except Exception:
                    logging.exception("Failed to decode packet %r from %s", data, addr)
            else:
                logging.debug("Skipping unknown packet %r from %s", data, addr)
    except socket.timeout:
        pass
    finally:
        s.close()

def main():
    try:
        scan()
    except IOError as exp:
        logging.error(exp)
        return exp.errno
    except Exception:
        return 128

    return 0

if __name__ == '__main__':
    ret = main()
    exit(ret)

