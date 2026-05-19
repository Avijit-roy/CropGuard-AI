import subprocess
import time
import sys
import os

def kill_port(port):
    """Kills any process running on the specified port."""
    try:
        import subprocess
        # Find PIDs using the port
        result = subprocess.check_output(["lsof", "-t", f"-i:{port}"], stderr=subprocess.DEVNULL)
        pids = result.decode().strip().split("\n")
        for pid in pids:
            if pid:
                print(f"⚠️ Port {port} in use by PID {pid}. Cleaning up...")
                subprocess.run(["kill", "-9", pid], check=False)
    except:
        pass

def main():
    print("========================================")
    print("🌿 Starting CropGuard AI System")
    print("========================================")
    
    # Ensure port 5000 is free
    kill_port(5000)
    
    processes = []
    
    try:
        # 1. Start MQTT Subscriber (which also runs the Dashboard API now!)
        print("[1/3] Starting MQTT Subscriber & API Server...")
        sub_process = subprocess.Popen([sys.executable, "backend/mqtt_subscriber.py"])
        processes.append(sub_process)
        time.sleep(3) # Give it a moment to initialize database and server
        
        # 2. Start MQTT Publisher (reads from Arduino)
        # Skip this on Render since the hardware is local to the user, not in the cloud
        if os.environ.get("RENDER"):
            print("[2/3] Skipping MQTT Publisher (Running on Render, expects local hardware)")
        else:
            print("[2/3] Starting MQTT Publisher (Serial to MQTT)...")
            pub_process = subprocess.Popen([sys.executable, "iot/mqtt_publisher.py"])
            processes.append(pub_process)
        
        # 3. Start Streamlit Frontend
        print("[3/3] Starting Streamlit Dashboard...")
        streamlit_process = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "frontend/app.py"])
        processes.append(streamlit_process)
        
        print("\n✅ All systems are running! Press Ctrl+C in this terminal to shut everything down cleanly.\n")
        
        # Wait for Streamlit to close (or user presses Ctrl+C)
        streamlit_process.wait()
        
    except KeyboardInterrupt:
        print("\n🛑 Shutting down all processes cleanly...")
    finally:
        for p in processes:
            p.terminate()
        print("Done.")

if __name__ == "__main__":
    main()
