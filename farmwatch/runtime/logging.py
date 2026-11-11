"""UTF-8 日志、按天轮转和第三方 stdout 的统一收集。"""
import io
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import re
import sys
import time


class SecretFilter(logging.Filter):
    def __init__(self, secrets):
        super().__init__()
        self.secrets = tuple(s for s in secrets if s)

    def filter(self, record):
        message = record.getMessage()
        for secret in self.secrets:
            message = message.replace(secret, '[redacted]')
        record.msg, record.args = message, ()
        return True


class SecretFormatter(logging.Formatter):
    def __init__(self, secrets):
        super().__init__('%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        self.secrets = tuple(s for s in secrets if s)

    def format(self, record):
        text = super().format(record)
        for secret in self.secrets:
            text = text.replace(secret, '[redacted]')
        return text


class LineLogger(io.TextIOBase):
    def __init__(self, logger, error=False):
        super().__init__()
        self.logger, self.error, self.pending = logger, error, ''

    @property
    def encoding(self):
        return 'utf-8'

    def write(self, text):
        self.pending += str(text)
        while '\n' in self.pending:
            line, self.pending = self.pending.split('\n', 1)
            self._emit(line)
        return len(text)

    def _emit(self, line):
        if not line.strip():
            return
        level = logging.ERROR if self.error or '❌' in line else logging.WARNING if '⚠️' in line else logging.INFO
        if 'Warning:' in line or 'warnings.warn(' in line:
            level = logging.WARNING
        if '未匹配' in line or ('score=' in line and ".png'" in line):
            level = logging.DEBUG
        self.logger.log(level, line.rstrip())

    def flush(self):
        if self.pending:
            self._emit(self.pending)
            self.pending = ''


def configure_logging(settings, *, console=True):
    directory = settings.root / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('farmwatch')
    logger.setLevel(settings.log_level)
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    file_handler = TimedRotatingFileHandler(directory / 'farmwatch.log', when='midnight',
                                            backupCount=settings.log_retention_days, encoding='utf-8')
    handlers = [file_handler]
    if console and sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    for handler in handlers:
        handler.setFormatter(SecretFormatter([settings.notify_spt, settings.notify_sendkey]))
        handler.addFilter(SecretFilter([settings.notify_spt, settings.notify_sendkey]))
        logger.addHandler(handler)
    cutoff = time.time() - settings.log_retention_days * 86400
    for path in directory.glob('farmwatch.log.*'):
        if re.fullmatch(r'farmwatch\.log\.\d{4}-\d{2}-\d{2}', path.name):
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                path.unlink()
    for path in directory.glob('farmwatch_bootstrap_*.log'):
        if re.fullmatch(r'farmwatch_bootstrap_\d{8}_\d{6}_[0-9a-f]{8}\.log', path.name):
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                path.unlink()
    return logger
