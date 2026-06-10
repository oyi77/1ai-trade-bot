module.exports = {
  apps: [
    {
      name: "1ai-trade-bot",
      script: "/home/linuxbrew/.linuxbrew/bin/python3",
      args: "-m tradebot --host 0.0.0.0 --port 8888",
      cwd: "/home/openclaw/projects/1ai-trade-bot",
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: "500M",
      error_file: "/var/log/tradebot/error.log",
      out_file: "/var/log/tradebot/out.log",
      log_file: "/var/log/tradebot/combined.log",
      time: true,
      env: {
        NODE_ENV: "production",
        PYTHONUNBUFFERED: "1",
      },
      merge_logs: true,
      listen_timeout: 10000,
      kill_timeout: 5000,
    },
  ],
};
