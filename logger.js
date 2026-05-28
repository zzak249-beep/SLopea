/**
 * Logger — Winston wrapper
 */
const winston = require('winston');

const { combine, timestamp, printf, colorize } = winston.format;

const fmt = printf(({ level, message, timestamp }) => {
  return `${timestamp} [${level.toUpperCase()}] ${message}`;
});

const logger = winston.createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: combine(
    timestamp({ format: 'YYYY-MM-DD HH:mm:ss' }),
    fmt
  ),
  transports: [
    new winston.transports.Console({
      format: combine(
        colorize(),
        timestamp({ format: 'HH:mm:ss' }),
        fmt
      ),
    }),
  ],
});

module.exports = logger;
