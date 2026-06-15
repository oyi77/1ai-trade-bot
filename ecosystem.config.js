module.exports = {
  apps: [
    {
      name: 'vilona-bot',
      script: 'scripts/_legacy/vilona_tradefx_handler.py',
      interpreter: 'python3',
      cwd: '/home/openclaw/projects/1ai-trade-bot',
      env: {
        PYTHONUNBUFFERED: '1',
      },
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      kill_timeout: 5000,
      wait_ready: true,
      listen_timeout: 10000,
    },
    {
      name: 'vilona-signal-bridge',
      script: 'scripts/_legacy/vilona_tradefx_signal_bridge.py',
      interpreter: 'python3',
      cwd: '/home/openclaw/projects/1ai-trade-bot',
      args: '--port 8765 --host 0.0.0.0',
      env: {
        PYTHONUNBUFFERED: '1',
      },
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      kill_timeout: 5000,
    },
  ],
};