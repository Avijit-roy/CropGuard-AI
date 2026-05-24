import subprocess
import time
import sys
import os

def kill_port(port):
    """Kill any process using the specified port."""
    try:
        result = subprocess.check_output(["lsof", "-t", f"-i:{port}"], stderr=subprocess.DEVNULL)
        pids = result.decode().strip().split("\n")
        for pid in pids:
            if pid:
                print(f"⚠️  Port {port} in use by PID {pid}. Cleaning up...")
                subprocess.run(["kill", "-9", pid], check=False)
    except:
        pass

def main():
    print("=" * 48)
    print("🌿  CropGuard AI — Flask Edition")
    print("=" * 48)

    kill_port(5000)

    processes = []

    try:
        # 1. Flask API Server & DB Manager
        print("[1/2] Starting Backend Server...")
        sub_process = subprocess.Popen(
            [sys.executable, "backend/server.py"],
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        processes.append(sub_process)
        time.sleep(4)  # Give the API + model time to load

        # 2. Local Serial Reader (reads Arduino and POSTs to API)
        if os.environ.get("RENDER"):
            print("[2/2] Skipping Local Serial Reader (cloud deployment — local hardware not present)")
        else:
            print("[2/2] Starting Local USB Serial Reader...")
            pub_process = subprocess.Popen(
                [sys.executable, "backend/serial_reader.py"],
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            processes.append(pub_process)

        print()
        print("✅  All systems running!")
        print("   Open your browser at:  https://cropguard-ai-1-ys7p.onrender.com")
        print("   Press Ctrl+C to shut everything down.\n")

        # Wait until subscriber exits (or Ctrl+C)
        sub_process.wait()

    except KeyboardInterrupt:
        print("\n🛑  Shutting down all processes cleanly...")
    finally:
        for p in processes:
            p.terminate()
        print("Done.")

if __name__ == "__main__":
    main()
