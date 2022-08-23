#!/usr/bin/env python3

import logging
import socket

# logging.getLogger().setLevel(logging.DEBUG)

# You need to be root to bind to port 23
port = 23
# Magic packet to which the controller will answer back
magic = b"\xff\x04\x02\xfb"

def scan():

    # Find all the broadcast addresses possible (one or more per network interfaces)
    # In the ideal world, we could just use '<broadcast>', but if there is no
    # gateway to the WAN, it will not work, and if there are several network
    # interfaces, only the one connected to the WAN will be scanned.
    bdc = set()
    try:
        import netifaces
        for itf in netifaces.interfaces():
            try:
                for addrinfo in netifaces.ifaddresses(itf)[socket.AF_INET]:
                    bdc.add(addrinfo["broadcast"])
            except KeyError:
                pass # no INET or no "broadcast"
    except ImportError:
        logging.info("No netifaces module, will fall back to generic broadcast")
        bdc.add(b"<broadcast>")

    for bdcaddr in bdc:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind((b'', port))  # bind before connect

            logging.debug("Broadcasting on %s:%d", bdcaddr, port)
            s.sendto(magic, (bdcaddr, port))
            s.settimeout(1.0)  # 1s max to answer back

            while True:
                data, (addr, p) = s.recvfrom(1024)
                if not data:
                    break
                elif data == magic:
                    logging.debug("Skipping our own packet")
                    continue
                elif data.startswith(b"\xfe") and len(data) > 25: # That should be an answer packet
                    try:
                        # Look for the hostname (default is like "8742-15433\x00")
                        end_hn = data.index(b"\x00", 19)
                        hn = data[19:end_hn].decode("latin1")
                        print("%s\t%s\t%d" % (hn, addr, p))
                    except Exception:
                        logging.exception("Failed to decode packet %r from %s",
                                          data, addr)
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
        logging.exception(exp)
        return exp.errno
    except Exception:
        logging.exception("Failed to scan the network")
        return 128

    return 0

if __name__ == '__main__':
    ret = main()
    exit(ret)
