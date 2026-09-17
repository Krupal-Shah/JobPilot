"""Run both development servers in one terminal; Ctrl+C stops both."""
import socket
import subprocess
import sys
import time
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    for port in (5050, 8421):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                print(f'Port {port} is occupied. Stop the existing server and retry.', flush=True)
                return 1
    processes = []
    try:
        for module, port in (('app', 5050), ('extension_server', 8421)):
            processes.append(subprocess.Popen(
                [sys.executable, '-m', 'flask', '--app', module, 'run',
                 '--host', '127.0.0.1', '--port', str(port), '--no-reload'], cwd=root,
            ))
        print('Website: http://127.0.0.1:5050 | Extension: http://127.0.0.1:8421\n'
              'Press Ctrl+C to stop both servers.', flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.25)
        print('A server exited; stopping the other server.', flush=True)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    sys.exit(main())
