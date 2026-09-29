"""
agent_service.py — Windows Service Wrapper for BehEDR Agent

Runs agent.py as a proper Windows service with:
- Auto-start on boot
- Auto-restart on crash
- Proper logging to Windows Event Log
- Service control (start/stop/restart)

Installation:
    python agent_service.py install
    python agent_service.py start

Removal:
    python agent_service.py stop
    python agent_service.py remove
"""

import sys
import os
import time
import servicemanager
import win32serviceutil
import win32service
import win32event
import logging
from pathlib import Path

# Add agent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class BehEDRAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "BehEDRAgent"
    _svc_display_name_ = "BehEDR Security Agent"
    _svc_description_ = "Behavioral Endpoint Detection & Response Agent - monitors processes, network, and file system for threats"
    
    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.is_running = True
        
        # Setup logging
        log_path = Path(__file__).parent / "agent_service.log"
        logging.basicConfig(
            filename=str(log_path),
            level=logging.INFO,
            format='%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.logger = logging.getLogger("BehEDRService")

    def SvcStop(self):
        """Called when service is stopped"""
        self.logger.info("Service stop requested")
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        self.is_running = False

    def SvcDoRun(self):
        """Main service loop"""
        self.logger.info("BehEDR Agent Service starting...")
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, '')
        )
        
        try:
            self.main()
        except Exception as e:
            self.logger.error(f"Service crashed: {e}", exc_info=True)
            servicemanager.LogErrorMsg(f"BehEDR Agent crashed: {e}")

    def main(self):
        """Main service logic - runs agent with auto-restart on crash"""
        import agent
        
        self.logger.info("Starting agent monitoring loop...")
        
        while self.is_running:
            try:
                # Import and run agent
                # The agent's main loop will run until stopped
                self.logger.info("Agent starting...")
                
                # Start agent threads
                import threading
                
                # Start monitor thread
                monitor_thread = threading.Thread(
                    target=agent._monitor_loop,
                    name="MonitorThread",
                    daemon=True
                )
                monitor_thread.start()
                
                # Start heartbeat thread
                hb_thread = threading.Thread(
                    target=self._heartbeat_loop,
                    args=(agent,),
                    name="HeartbeatThread",
                    daemon=True
                )
                hb_thread.start()
                
                # Start command poll thread
                cmd_thread = threading.Thread(
                    target=self._command_loop,
                    args=(agent,),
                    name="CommandThread",
                    daemon=True
                )
                cmd_thread.start()
                
                # Start sender thread
                sender_thread = threading.Thread(
                    target=agent._sender_loop,
                    name="SenderThread",
                    daemon=True
                )
                sender_thread.start()
                
                # Start network monitor thread
                net_thread = threading.Thread(
                    target=agent._network_loop,
                    name="NetworkThread",
                    daemon=True
                )
                net_thread.start()
                
                # Start file monitor thread
                file_thread = threading.Thread(
                    target=agent._file_loop,
                    name="FileThread",
                    daemon=True
                )
                file_thread.start()
                
                # Start DLL monitor thread (if processes found)
                # DLL monitoring is integrated in _monitor_loop
                
                self.logger.info("All agent threads started successfully")
                
                # Wait for stop signal
                while self.is_running:
                    # Check stop event every 5 seconds
                    if win32event.WaitForSingleObject(self.stop_event, 5000) == win32event.WAIT_OBJECT_0:
                        self.logger.info("Stop event received, shutting down...")
                        agent._stop.set()
                        break
                
                # Wait for threads to finish
                time.sleep(2)
                self.logger.info("Agent stopped cleanly")
                break
                
            except Exception as e:
                self.logger.error(f"Agent crashed: {e}", exc_info=True)
                
                if not self.is_running:
                    break
                
                self.logger.info("Restarting agent in 10 seconds...")
                time.sleep(10)
    
    def _heartbeat_loop(self, agent):
        """Send heartbeat to server"""
        while self.is_running:
            try:
                agent._post(agent.HB_URL, {"system_id": agent.SYSTEM_ID}, "HEARTBEAT")
            except Exception as e:
                self.logger.debug(f"Heartbeat error: {e}")
            time.sleep(agent.HB_SECS)
    
    def _command_loop(self, agent):
        """Poll for commands from server"""
        while self.is_running:
            try:
                resp = agent._get(agent.CMDS_URL, "COMMANDS")
                if resp and isinstance(resp, list):
                    for cmd in resp:
                        self._execute_command(agent, cmd)
            except Exception as e:
                self.logger.debug(f"Command poll error: {e}")
            time.sleep(agent.CMD_SECS)
    
    def _execute_command(self, agent, cmd):
        """Execute a command received from server"""
        try:
            action = cmd.get("action", "")
            self.logger.info(f"Executing command: {action}")
            
            if action == "restart_agent":
                self.logger.info("Restart requested, stopping service...")
                self.SvcStop()
            # Add other commands as needed
            
        except Exception as e:
            self.logger.error(f"Command execution error: {e}")


def main():
    """Entry point for service installation"""
    if len(sys.argv) == 1:
        # Called by Windows Service system
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(BehEDRAgentService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        # Called from command line for install/remove/start/stop
        win32serviceutil.HandleCommandLine(BehEDRAgentService)


if __name__ == '__main__':
    main()
