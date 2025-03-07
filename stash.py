# coding: utf-8
"""
StaSh - Pythonista Shell

https://github.com/ywangd/stash
"""

__version__ = '0.5.4'

import os
import sys
from ConfigParser import ConfigParser
from StringIO import StringIO
import imp as pyimp  # rename to avoid name conflict with objc_util
import logging
import logging.handlers


# noinspection PyPep8Naming
from system.shcommon import IN_PYTHONISTA, ON_IPAD
from system.shcommon import _STASH_ROOT, _STASH_CONFIG_FILES, _SYS_STDOUT
from system.shcommon import Graphics as graphics, Control as ctrl, Escape as esc
from system.shparsers import ShParser, ShExpander, ShCompleter
from system.shruntime import ShRuntime
from system.shstreams import ShMiniBuffer, ShStream
from system.shscreens import ShSequentialScreen, ShSequentialRenderer
from system.shui import ShUI
from system.shio import ShIO


# Setup logging
LOGGER = logging.getLogger('StaSh')

# Debugging constants
_DEBUG_STREAM = 200
_DEBUG_RENDERER = 201
_DEBUG_MAIN_SCREEN = 202
_DEBUG_MINI_BUFFER = 203
_DEBUG_IO = 204
_DEBUG_UI = 300
_DEBUG_TERMINAL = 301
_DEBUG_TV_DELEGATE = 302
_DEBUG_RUNTIME = 400
_DEBUG_PARSER = 401
_DEBUG_EXPANDER = 402
_DEBUG_COMPLETER = 403


# Default configuration (can be overridden by external configuration file)
_DEFAULT_CONFIG = """[system]
rcfile=.stashrc
py_traceback=0
py_pdb=0
input_encoding_utf8=1
ipython_style_history_search=1
thread_type=ctypes

[display]
TEXT_FONT_SIZE={text_size}
BUTTON_FONT_SIZE=14
BACKGROUND_COLOR=(0.0, 0.0, 0.0)
TEXT_COLOR=(1.0, 1.0, 1.0)
TINT_COLOR=(0.0, 0.0, 1.0)
INDICATOR_STYLE=white
HISTORY_MAX=50
BUFFER_MAX=150
AUTO_COMPLETION_MAX=50
VK_SYMBOLS=~/.-*|>$'=!&_"\?`
""".format(text_size=14 if ON_IPAD else 12)


class StaSh(object):
    """
    Main application class. It initialize and wires the components and provide
    utility interfaces to running scripts.
    """

    def __init__(self, debug=(), log_setting=None):
        self.__version__ = __version__

        self.config = self._load_config()
        self.logger = self._config_logging(log_setting)

        # Tab handler for running scripts
        self.external_tab_handler = None

        # Wire the components
        self.main_screen = ShSequentialScreen(self,
                                              nlines_max=self.config.getint('display', 'BUFFER_MAX'),
                                              debug=_DEBUG_MAIN_SCREEN in debug)

        self.mini_buffer = ShMiniBuffer(self,
                                        self.main_screen,
                                        debug=_DEBUG_MINI_BUFFER in debug)

        self.stream = ShStream(self,
                               self.main_screen,
                               debug=_DEBUG_STREAM in debug)

        self.io = ShIO(self, debug=_DEBUG_IO in debug)

        self.terminal = None  # will be set during UI initialisation
        self.ui = ShUI(self, debug=_DEBUG_UI in debug)
        self.renderer = ShSequentialRenderer(self.main_screen, self.terminal,
                                             debug=_DEBUG_RENDERER in debug)

        parser = ShParser(debug=_DEBUG_PARSER in debug)
        expander = ShExpander(self, debug=_DEBUG_EXPANDER in debug)
        self.runtime = ShRuntime(self, parser, expander, debug=_DEBUG_RUNTIME in debug)
        self.completer = ShCompleter(self, debug=_DEBUG_COMPLETER in debug)

        # Navigate to the startup folder
        if IN_PYTHONISTA:
            os.chdir(self.runtime.envars['HOME2'])
        self.runtime.load_rcfile()
        self.io.write(self.text_style('StaSh v%s\n' % self.__version__,
                                      {'color': 'blue', 'traits': ['bold']}))
        self.runtime.script_will_end()  # configure the read callback

        # Load shared libraries
        self._load_lib()

    def __call__(self, *args, **kwargs):
        """ This function is to be called by external script for
         executing shell commands """
        worker = self.runtime.run(*args, **kwargs)
        worker.join()

    @staticmethod
    def _load_config():
        config = ConfigParser()
        config.optionxform = str  # make it preserve case
        # defaults
        config.readfp(StringIO(_DEFAULT_CONFIG))
        # update from config file
        config.read(os.path.join(_STASH_ROOT, f) for f in _STASH_CONFIG_FILES)

        return config

    @staticmethod
    def _config_logging(log_setting):

        logger = logging.getLogger('StaSh')

        _log_setting = {
            'level': 'DEBUG',
            'stdout': True,
        }

        _log_setting.update(log_setting or {})

        level = {
            'CRITICAL': logging.CRITICAL,
            'ERROR': logging.ERROR,
            'WARNING': logging.WARNING,
            'INFO': logging.INFO,
            'DEBUG': logging.DEBUG,
            'NOTEST': logging.NOTSET,
        }.get(_log_setting['level'], logging.DEBUG)

        logger.setLevel(level)

        if not logger.handlers:
            if _log_setting['stdout']:
                _log_handler = logging.StreamHandler(_SYS_STDOUT)
            else:
                _log_handler = logging.handlers.RotatingFileHandler('stash.log', mode='w')
            _log_handler.setLevel(level)
            _log_handler.setFormatter(logging.Formatter(
                '[%(asctime)s] [%(levelname)s] [%(threadName)s] [%(name)s] [%(funcName)s] [%(lineno)d] - %(message)s'
            ))
            logger.addHandler(_log_handler)

        return logger

    def _load_lib(self):
        """
        Load library files as modules and save each of them as attributes
        """
        lib_path = os.path.join(_STASH_ROOT, 'lib')
        saved_environ = dict(os.environ)
        os.environ.update(self.runtime.envars)
        try:
            for f in os.listdir(lib_path):
                if f.startswith('lib') and f.endswith('.py') \
                        and os.path.isfile(os.path.join(lib_path, f)):
                    name, _ = os.path.splitext(f)
                    try:
                        self.__dict__[name] = pyimp.load_source(name, os.path.join(lib_path, f))
                    except Exception as e:
                        self.write_message('%s: failed to load library file (%s)' % (f, repr(e)))
        finally:
            os.environ = saved_environ

    def write_message(self, s):
        self.io.write('stash: %s\n' % s)

    def launch(self, style='panel'):
        self.ui.present(style)
        self.terminal.begin_editing()

    # noinspection PyProtectedMember
    @staticmethod
    def text_style(s, style, always=False):
        """
        Style the given string with ASCII escapes.

        :param str s: String to decorate
        :param dict style: A dictionary of styles
        :param bool always: If true, style will be applied even for pipes.
        :return:
        """
        # No color for pipes
        if not always and (isinstance(sys.stdout, StringIO) or isinstance(sys.stdout, file)):
            return s

        fmt_string = u'%s%%d%s%%s%s%%d%s' % (ctrl.CSI, esc.SGR, ctrl.CSI, esc.SGR)
        for style_name, style_value in style.items():
            if style_name == 'color':
                color_id = graphics._SGR.get(style_value.lower())
                if color_id is not None:
                    s = fmt_string % (color_id, s, graphics._SGR['default'])
            elif style_name == 'bgcolor':
                color_id = graphics._SGR.get('bg-' + style_value.lower())
                if color_id is not None:
                    s = fmt_string % (color_id, s, graphics._SGR['default'])
            elif style_name == 'traits':
                for val in style_value:
                    val = val.lower()
                    if val == 'bold':
                        s = fmt_string % (graphics._SGR['+bold'], s, graphics._SGR['-bold'])
                    elif val == 'italic':
                        s = fmt_string % (graphics._SGR['+italics'], s, graphics._SGR['-italics'])
                    elif val == 'underline':
                        s = fmt_string % (graphics._SGR['+underscore'], s, graphics._SGR['-underscore'])
                    elif val == 'strikethrough':
                        s = fmt_string % (graphics._SGR['+strikethrough'], s, graphics._SGR['-strikethrough'])

        return s

    def text_color(self, s, color_name='default', **kwargs):
        return self.text_style(s, {'color': color_name}, **kwargs)

    def text_bgcolor(self, s, color_name='default', **kwargs):
        return self.text_style(s, {'bgcolor': color_name}, **kwargs)

    def text_bold(self, s, **kwargs):
        return self.text_style(s, {'traits': ['bold']}, **kwargs)

    def text_italic(self, s, **kwargs):
        return self.text_style(s, {'traits': ['italic']}, **kwargs)

    def text_bold_italic(self, s, **kwargs):
        return self.text_style(s, {'traits': ['bold', 'italic']}, **kwargs)

    def text_underline(self, s, **kwargs):
        return self.text_style(s, {'traits': ['underline']}, **kwargs)

    def text_strikethrough(self, s, **kwargs):
        return self.text_style(s, {'traits': ['strikethrough']}, **kwargs)


if __name__ == '__main__':
    _stash = StaSh()
    _stash.launch()
