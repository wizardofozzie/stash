# coding: utf-8
import logging
import re

# noinspection PyPep8Naming
from .shcommon import Control as ctrl, Escape as esc


class ShMiniBuffer(object):

    """
    This class process user inputs (as opposed to running scripts I/O). It is
    called by the UI delegate to process the text_view_should_change event.
    """

    RANGE_BUFFER_END = 'RANGE_BUFFER_END'
    RANGE_MODIFIABLE_CHARS = 'RANGE_MODIFIABLE_CHARS'

    def __init__(self, stash, main_screen, debug=False):
        self.stash = stash
        self.main_screen = main_screen
        self.debug = debug
        self.logger = logging.getLogger('StaSh.MiniBuffer')

        self.chars = ''  # buffer that holds incoming chars from user
        self.runtime_callback = None
        # TODO: cbreak mode, process char by char. NOT IMPLEMENTED
        self.cbreak = False

        self._pattern_word_split = re.compile('[^\W]+\W*')

    def feed(self, rng, replacement):
        """
        Directly called by a TextView delegate to replace existing chars
        in given range with the given new chars.

        :param shterminal.ShTerminal terminal: The terminal object
        :param (int, int) | None | str rng: the range of selected chars
        :param str replacement: new chars
        :return:
        """

        if rng is None or rng == self.RANGE_MODIFIABLE_CHARS:
            rng_adjusted = (self.x_modifiable, len(self.chars))
        elif rng == self.RANGE_BUFFER_END:
            rng_adjusted = (len(self.chars), len(self.chars))
        else:
            # Convert and adjust the range relative to the input buffer
            rng_adjusted = self._adjust_range(rng)

        # Lock the main_screen for modification
        with self.main_screen.acquire_lock():
            self._ensure_main_screen_consistency()

            # Delete contents of selected range first
            if rng_adjusted[0] != rng_adjusted[1]:
                if self.debug:
                    self.logger.debug('DELETING %s' % str(rng_adjusted))
                self.chars = self.chars[:rng_adjusted[0]] + self.chars[rng_adjusted[1]:]
                self.main_screen.replace_in_range(
                    (rng_adjusted[0] - self.x_modifiable, rng_adjusted[1] - self.x_modifiable),
                    '',
                    relative_to_x_modifiable=True)
        # Lock is now released

        if replacement == '':  # pure deletion
            self.stash.renderer.render(no_wait=True)

        elif replacement == '\t':  # TODO: Separate tab manager

            tab_handler = (self.stash.completer.complete if not self.stash.runtime.worker_stack
                           else self.stash.external_tab_handler)

            if callable(tab_handler):
                incomplete = self.chars[self.x_modifiable: rng_adjusted[0]]
                try:
                    completed, possibilities = tab_handler(incomplete)

                    if completed != incomplete:
                        with self.main_screen.acquire_lock():
                            self.modifiable_chars = completed + self.chars[rng_adjusted[0]:]
                            self.main_screen.modifiable_chars = self.modifiable_chars
                            self.main_screen.cursor_x = self.main_screen.x_modifiable + len(completed)

                    elif len(possibilities) > 0:  # TODO: handle max possibilities checking
                        # Run through stream feed to allow attributed texts to be processed
                        self.stash.stream.feed(
                            u'\n%s\n%s' % ('  '.join(possibilities), self.stash.runtime.get_prompt()),
                            render_it=False  # do not render to avoid dead lock on UI thread
                        )
                        with self.main_screen.acquire_lock():
                            self.main_screen.modifiable_chars = self.modifiable_chars
                            self.main_screen.cursor_x = self.main_screen.x_modifiable + len(incomplete)

                    else:  # no completion can be achieved
                        with self.main_screen.acquire_lock():
                            self.main_screen.modifiable_chars = self.modifiable_chars
                            self.main_screen.cursor_x = self.main_screen.x_modifiable + len(incomplete)

                except Exception as e:  # TODO: better error handling
                    self.stash.stream.feed(
                        u'\nauto-completion error: %s\n%s' % (repr(e), self.stash.runtime.get_prompt()),
                        render_it=False)
                    with self.main_screen.acquire_lock():
                        self.main_screen.modifiable_chars = self.modifiable_chars
                        self.main_screen.cursor_x = self.main_screen.x_modifiable + len(incomplete)

                self.stash.renderer.render(no_wait=True)
            else:
                # TODO: simply add the tab character or show a warning?
                pass  # do nothing for now

        else:  # process line by line
            # TODO: Ideally the input should be processed by character. But it is slow.
            x = rng_adjusted[0]  # The location where character to be inserted
            for rpln in replacement.splitlines(True):

                # Lock the main_screen for modification
                with self.main_screen.acquire_lock():
                    self._ensure_main_screen_consistency()

                    # Update the mini buffer and the main_screen buffer
                    if rpln.endswith('\n'):  # LF is always added to the end of the line
                        if len(rpln) > 1:  # not a pure return char
                            self.main_screen.replace_in_range(
                                (x - self.x_modifiable, x - self.x_modifiable),
                                rpln[:-1],
                                relative_to_x_modifiable=True)
                        self.main_screen.replace_in_range(
                            None,
                            u'\n',
                            relative_to_x_modifiable=False)
                        self.chars = self.chars[:x] + rpln[:-1] + self.chars[x:] + '\n'
                    else:
                        # Do not send NULL char to main screen, it crashes the app
                        if rpln != '\0':
                            self.main_screen.replace_in_range(
                                (x - self.x_modifiable, x - self.x_modifiable),
                                rpln,
                                relative_to_x_modifiable=True)
                        self.chars = self.chars[:x] + rpln + self.chars[x:]
                # Lock is now released

                # After the first line, the range should now always be at the end
                x = len(self.chars)

                # Render after every line
                self.stash.renderer.render(no_wait=True)

            # If complete lines or EOF are available, push them to IO buffer and notify
            # runtime for script running if no script is currently running.
            idx_lf = max(self.chars.rfind('\n'), self.chars.rfind('\0'))
            if idx_lf != -1:
                self.stash.io.push(self.chars[:idx_lf + 1])
                self.chars = self.chars[idx_lf + 1:]
                if self.runtime_callback is not None:
                    # When a script is running, all input are considered directed
                    # to the running script.
                    callback, self.runtime_callback = self.runtime_callback, None
                    callback()

    def set_cursor(self, offset, whence=0):
        """
        Set cursor in the modifiable range.
        :param offset:
        :param whence:
        """
        # Lock the main_screen for modification
        with self.main_screen.acquire_lock():
            self._ensure_main_screen_consistency()

            modifiable_length = len(self.modifiable_chars)

            if whence == 1:  # current position
                self.main_screen.cursor_x += offset
            elif whence == 2:  # from the end
                self.main_screen.cursor_x = self.main_screen.x_modifiable + modifiable_length + offset
            else:
                self.main_screen.cursor_x = self.main_screen.x_modifiable + offset  # default from start

            self.main_screen.ensure_cursor_in_modifiable_range()

        self.stash.renderer.render(no_wait=True)

    def delete_word(self, rng):
        if rng[0] != rng[1]:  # do nothing if there is any selection
            return

        modifiable_chars = self.modifiable_chars  # nothing to be deleted
        if len(self.modifiable_chars) == 0:
            return

        rng_adjusted = self._adjust_range(rng)
        deletable_chars = modifiable_chars[: rng_adjusted[0]]
        left_chars = ''.join(self._pattern_word_split.findall(deletable_chars)[:-1])
        self.modifiable_chars = left_chars + modifiable_chars[rng_adjusted[0]:]
        self.main_screen.modifiable_chars = self.modifiable_chars
        self.set_cursor(len(left_chars))

        self.stash.renderer.render(no_wait=True)

    def _adjust_range(self, rng):
        """
        Convert the incoming range (by user) to values relative to the
        input buffer text. Also enforce the modifiable bound.
        :param (int, int) rng: range of selected text
        :return: (int, int): Adjusted range
        """
        terminal = self.stash.terminal
        tv_text = terminal.text  # existing text from the terminal
        length = len(self.chars)  # length of the existing input buffer

        # If the modifiable chars are different from the trailing chars on terminal,
        # this means additional output has been put on the terminal
        # after the event. In this case, simply set the range at the end of
        # the existing input buffer.
        modifiable_chars = self.modifiable_chars
        if modifiable_chars != '' and tv_text[-len(modifiable_chars):] != modifiable_chars:
            xs_adjusted = xe_adjusted = length

        else:
            xs, xe = rng
            # The start location is converted using it offset to the end of the
            # terminal text.
            xs_adjusted = length - (len(tv_text) - xs)
            if xs_adjusted < self.x_modifiable:
                # the selection is invalid because it starts beyond the modifiable input buffer
                xs_adjusted = xe_adjusted = length
            else:
                xe_adjusted = xs_adjusted + (xe - xs)

        return xs_adjusted, xe_adjusted

    def _ensure_main_screen_consistency(self):
        # If the main screen's modifiable character is different from the input
        # buffer, it means more output has been put onto the main screen after
        # last update from the mini buffer. So the modifiable_chars need to be
        # reset at the new x_modifiable location.
        if self.modifiable_chars != self.main_screen.modifiable_chars:
            if self.debug:
                self.logger.debug('Inconsistent mini_buffer [%s] main_screen [%s]' %
                                  (self.modifiable_chars, self.main_screen.modifiable_chars))
            self.main_screen.modifiable_chars = self.modifiable_chars

    def config_runtime_callback(self, callback):
        self.runtime_callback = callback

    @property
    def x_modifiable(self):
        """
        The index where chars start to be modifiable. Modifiable chars are
        those input text that can still be edited by users. Any characters
        before a linebreak is not modifiable.
        :rtype: int
        """
        idx = self.chars.rfind('\n')
        return idx + 1 if idx != -1 else 0

    @property
    def modifiable_chars(self):
        """
        :rtype: str: modifiable characters
        """
        return self.chars[self.x_modifiable:]

    @modifiable_chars.setter
    def modifiable_chars(self, value):
        """
        :param str value: New value for the modifiable chars
        """
        self.chars = self.chars[: self.x_modifiable] + value


class ShStream(object):
    """
    This class is to process I/O from running scripts (as opposed to user input).

    A stream is a state machine that parses a stream of characters
    and dispatches events based on what it sees.
    """

    #: CSI escape sequences -- ``CSI P1;P2;...;Pn <fn>``.
    csi = {
        esc.RIS: "reset",
        esc.SGR: "select_graphic_rendition",
    }

    STATE_STREAM = 0
    STATE_ESCAPE = 1
    STATE_ARGUMENTS = 2

    def __init__(self, stash, main_screen, debug=False):

        self.consume_handlers = (self._stream, self._escape, self._arguments)

        self.stash = stash
        self.main_screen = main_screen
        self.debug = debug
        self.logger = logging.getLogger('StaSh.Stream')

        self.dispatch_handler = {
            'draw': self.main_screen.draw,
            'reset': self.main_screen.reset,
            'select_graphic_rendition': self.main_screen.select_graphic_rendition,
        }

        self.reset()

    def reset(self):
        """Reset state to ``"stream"`` and empty parameter attributes."""
        self.state = self.STATE_STREAM
        self.params = []
        self.current = ''

    def consume(self, char):
        """Consumes a single string character and advance the state as
        necessary.

        :param str char: a character to consume.
        """
        try:
            self.consume_handlers[self.state](char)
        except Exception as e:  # TODO: better error handling
            self.reset()

    def feed(self, chars, render_it=True, no_wait=False):
        """Consumes a string and advance the state as necessary.

        :param str chars: a string to feed from.
        """
        # To avoid the \xc2 deadlock from bytes string
        if not isinstance(chars, unicode):
            chars = chars.decode('utf-8', errors='ignore')

        with self.main_screen.acquire_lock():
            for char in chars:
                self.consume(char)

        if render_it:
            self.stash.renderer.render(no_wait=no_wait)

    def dispatch(self, event, *args, **kwargs):
        """Dispatches an event.

           If any of the attached listeners throws an exception, the
           subsequent callbacks are be aborted.

        :param str event: event to dispatch.
        :param list args: arguments to pass to event handlers.
        """

        self.dispatch_handler[event](*args)

        if kwargs.get("reset", True):
            self.reset()

    def _stream(self, char):
        """Processes a character when in the default ``"stream"`` state."""
        if char not in (ctrl.NUL, ctrl.DEL, ctrl.ESC, ctrl.CSI):
            self.dispatch("draw", char, reset=False)
        elif char == ctrl.ESC:
            self.state = self.STATE_ESCAPE
        elif char == ctrl.CSI:
            self.state = self.STATE_ARGUMENTS

    def _escape(self, char):
        """Handles characters seen when in an escape sequence.
        """
        if char == "[":
            self.state = self.STATE_ARGUMENTS
        else:  # TODO: all other escapes are ignored
            self.dispatch('draw', char)

    def _arguments(self, char):
        """Parses arguments of a CSI sequence.

        All parameters are unsigned, positive decimal integers, with
        the most significant digit sent first. Any parameter greater
        than 9999 is set to 9999. If you do not specify a value, a 0
        value is assumed.
        """
        if char.isdigit():
            self.current += char
        else:
            self.params.append(min(int(self.current or 0), 9999))
            if char == ";":  # multiple parameters
                self.current = ""
            else:
                self.dispatch(self.csi[char], *self.params)