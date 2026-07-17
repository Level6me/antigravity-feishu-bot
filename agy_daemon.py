import pexpect
import sys
import time

def main():
    print("Starting agy daemon...", flush=True)
    child = pexpect.spawn("/home/ubuntu/.local/bin/agy", encoding='utf-8')
    
    try:
        while True:
            line = child.readline()
            if not line:
                break
    except KeyboardInterrupt:
        pass
    except pexpect.EOF:
        pass
    print("agy daemon exited", flush=True)
    sys.exit(0)

if __name__ == "__main__":
    main()
