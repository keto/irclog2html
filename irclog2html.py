#!/usr/bin/env python
"""
Convert IRC logs to HTML.

Usage: irclog2html.py filename

irclog2html will write out a colourised irc log, appending a .html
extension to the output file.

This is a Python port (+ improvements) of irclog2html.pl Version 2.1, which
was written by Jeff Waugh and is available at www.perkypants.org
"""

# Copyright (c) 2005, Marius Gedminas 
# Copyright (c) 2000, Jeffrey W. Waugh

# Python port:
#   Marius Gedminas <marius@pov.lt>
# Original Author:
#   Jeff Waugh <jdub@perkypants.org>
# Contributors:
#   Rick Welykochy <rick@praxis.com.au>
#   Alexander Else <aelse@uu.net>
#
# Released under the terms of the GNU GPL
# http://www.gnu.org/copyleft/gpl.html

# Differences from the Perl version:
#   There are no hardcoded nick colour preferences for jdub, cantanker and
#   chuckd
#
#   Colours are preserver accross nick changes (irclog2html.pl tries to do
#   that, but its regexes are buggy)
#
#   irclog2html.pl interprets --colour-server #rrggbb as -s #rrggbb,
#   irclog2html.py does not have this bug
#
#   irclog2html.py understands ISO 8601 timestamps (YYYY-MM-DDTHH:MM:SS)
#
#   New options: --title, --{prev,index,next}-{url,title}
#
#   New styles: xhtml, xhtmltable
#

import os
import re
import sys
import urllib
import optparse

VERSION = "2.2"
RELEASE = "2005-02-04"

# $Id$


#
# Log parsing
#

class Enum(object):
    """Enumerated value."""

    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return self.value


class LogParser(object):
    """Parse an IRC log file.

    When iterated, yields the following events:

        time, COMMENT, (nick, text)
        time, ACTION, text
        time, JOIN, text
        time, PART, text,
        time, NICKCHANGE, (text, oldnick, newnick)
        time, SERVER, text

    """

    COMMENT = Enum('COMMENT')
    ACTION = Enum('ACTION')
    JOIN = Enum('JOIN')
    PART = Enum('PART')
    NICKCHANGE = Enum('NICKCHANGE')
    SERVER = Enum('SERVER')
    OTHER = Enum('OTHER')

    TIME_REGEXP = re.compile(
            r'^\[?(' # Optional [
            r'(?:\d{4}-\d{2}-\d{2}T|\d{2}-\w{3}-\d{4} )?' # Optional date
            r'\d\d:\d\d(:\d\d)?' # Mandatory HH:MM, optional :SS
            r')\]? +') # Optional ], mandatory space
    NICK_REGEXP = re.compile(r'^<(.*?)>\s')
    JOIN_REGEXP = re.compile(r'^(?:\*\*\*|-->)\s.*joined')
    PART_REGEXP = re.compile(r'^(?:\*\*\*|<--)\s.*(quit|left)')
    SERVMSG_REGEXP = re.compile(r'^(?:\*\*\*|---)\s')
    NICK_CHANGE_REGEXP = re.compile(
            r'^(?:\*\*\*|---)\s+(.*?) (?:are|is) now known as (.*)')

    def __init__(self, infile):
        self.infile = infile

    def __iter__(self):
        for line in self.infile:
            line = line.rstrip('\r\n')
            if not line:
                continue

            m = self.TIME_REGEXP.match(line)
            if m:
                time = m.group(1)
                line = line[len(m.group(0)):]
            else:
                time = None

            m = self.NICK_REGEXP.match(line)
            if m:
                nick = m.group(1)
                text = line[len(m.group(0)):]
                yield time, self.COMMENT, (nick, text)
            elif line.startswith('* ') or line.startswith('*\t'):
                yield time, self.ACTION, line
            elif self.JOIN_REGEXP.match(line):
                yield time, self.JOIN, line
            elif self.PART_REGEXP.match(line):
                yield time, self.PART, line
            else:
                m = self.NICK_CHANGE_REGEXP.match(line)
                if m:
                    oldnick = m.group(1)
                    newnick = m.group(2)
                    yield time, self.NICKCHANGE, (line, oldnick, newnick)
                elif self.SERVMSG_REGEXP.match(line):
                    yield time, self.SERVER, line
                else:
                    yield time, self.OTHER, line


def shorttime(time):
    """Strip date and seconds from time.

        >>> shorttime('12:45:17')
        '12:45'
        >>> shorttime('12:45')
        '12:45'
        >>> shorttime('2005-02-04T12:45')
        '12:45'

    """
    if 'T' in time:
        time = time.split('T')[-1]
    if time.count(':') > 1:
        time = ':'.join(time.split(':')[:2])
    return time


#
# Colouring stuff
#

class ColourChooser:
    """Choose distinguishable colours."""

    def __init__(self, rgbmin=240, rgbmax=125, rgb=None, a=0.95, b=0.5):
        """Define a range of colours available for choosing.

        `rgbmin` and `rgbmax` define the outmost range of colour depth (note
        that it is allowed to have rgbmin > rgbmax).

        `rgb`, if specified, is a list of (r,g,b) values where each component
        is between 0 and 1.0.

        If `rgb` is not specified, then it is constructed as
           [(a,b,b), (b,a,b), (b,b,a), (a,a,b), (a,b,a), (b,a,a)]

        You can tune `a` and `b` for the starting and ending concentrations of
        RGB.
        """
        assert 0 <= rgbmin < 256
        assert 0 <= rgbmax < 256
        self.rgbmin = rgbmin
        self.rgbmax = rgbmax
        if not rgb:
            assert 0 <= a <= 1.0
            assert 0 <= b <= 1.0
            rgb = [(a,b,b), (b,a,b), (b,b,a), (a,a,b), (a,b,a), (b,a,a)]
        else:
            for r, g, b in rgb:
                assert 0 <= r <= 1.0
                assert 0 <= g <= 1.0
                assert 0 <= b <= 1.0
        self.rgb = rgb

    def choose(self, i, n):
        """Choose a colour.

        `n` specifies how many different colours you want in total.
        `i` identifies a particular colour in a set of `n` distinguishable
        colours.

        Returns a string '#rrggbb'.
        """
        if n == 0:
            n = 1
        r, g, b = self.rgb[i % len(self.rgb)]
        m = self.rgbmin + (self.rgbmax - self.rgbmin) * float(n - i) / n
        r, g, b = map(int, (r * m, g * m, b * m))
        assert 0 <= r < 256
        assert 0 <= g < 256
        assert 0 <= b < 256
        return '#%02x%02x%02x' % (r, g, b)


class NickColourizer:
    """Choose distinguishable colours for nicknames."""

    def __init__(self, maxnicks=30, colour_chooser=None, default_colours=None):
        """Create a colour chooser for nicknames.

        If you know how many different nicks there might be, specify that
        numer as `maxnicks`.  If you don't know, don't worry.

        If you really want to, you can specify a colour chooser.  Default is
        ColourChooser().

        If you want, you can specify default colours for certain nicknames
        (`default_colours` is a mapping of nicknames to HTML colours, that is
        '#rrggbb' strings).
        """
        if colour_chooser is None:
            colour_chooser = ColourChooser()
        self.colour_chooser = colour_chooser
        self.nickcount = 0
        self.maxnicks = maxnicks
        self.nick_colour = {}
        if default_colours:
            self.nick_colour.update(default_colours)

    def __getitem__(self, nick):
        colour = self.nick_colour.get(nick)
        if not colour:
            self.nickcount += 1
            if self.nickcount >= self.maxnicks:
                self.maxnicks *= 2
            colour = self.colour_chooser.choose(self.nickcount, self.maxnicks)
            self.nick_colour[nick] = colour
        return colour

    def change(self, oldnick, newnick):
        if oldnick in self.nick_colour:
            self.nick_colour[newnick] = self.nick_colour.pop(oldnick)


#
# HTML
#

URL_REGEXP = re.compile(r'((http|https|ftp|gopher|news)://[^ \'")>]*)')

def createlinks(text):
    """Replace possible URLs with links."""
    return URL_REGEXP.sub(r'<a href="\1">\1</a>', text)

def escape(s):
    """Replace ampersands, pointies, control characters.

        >>> escape('Hello & <world>')
        'Hello &amp; &lt;world&gt;'
        >>> escape('Hello & <world>')
        'Hello &amp; &lt;world&gt;'

    Control characters (ASCII 0 to 31) are stripped away

        >>> escape(''.join([chr(x) for x in range(32)]))
        ''

    """
    s = s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return ''.join([c for c in s if ord(c) > 0x1F])


#
# Output styles
#

class AbstractStyle(object):
    """A style defines the way output is formatted.

    This is not a real class, rather it is an description of how style
    classes should be written.
    """

    name = "stylename"
    description = "Single-line description"

    def __init__(self, outfile, colours=None):
        """Create a text formatter for writing to outfile.

        `colours` may have the following attributes:
           part
           join
           server
           nickchange
           action
        """
        self.outfile = outfile
        self.colours = colours or {}

    def head(self, title, prev=('', ''), index=('', ''), next=('', '')):
        """Generate the header.

        `prev`, `index` and `next` are tuples (title, url) that comprise
        the navigation bar.
        """

    def foot(self):
        """Generate the footer."""

    def servermsg(self, time, what, line):
        """Output a generic server message.

        `time` is a string.
        `line` is not escaped.
        `what` is one of LogParser event constants (e.g. LogParser.JOIN).
        """

    def nicktext(self, time, nick, text, htmlcolour):
        """Output a comment uttered by someone.

        `time` is a string.
        `nick` and `text` are not escaped.
        `htmlcolour` is a string ('#rrggbb').
        """


class SimpleTextStyle(AbstractStyle):
    """Text style with little use of colour"""

    name = "simplett"
    description = __doc__

    def head(self, title, prev=None, index=None, next=None,
             charset="iso-8859-1"):
        print >> self.outfile, """\
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">
<html>
<head>
\t<title>%(title)s</title>
\t<meta name="generator" content="irclog2html.py %(VERSION)s by Marius Gedminas">
\t<meta name="version" content="%(VERSION)s - %(RELEASE)s">
\t<meta http-equiv="Content-Type" content="text/html; charset=%(charset)s">
</head>
<body text="#000000" bgcolor="#ffffff"><tt>""" % {
            'VERSION': VERSION,
            'RELEASE': RELEASE,
            'title': escape(title),
            'charset': charset,
        }

    def foot(self):
        print >> self.outfile, """
<br>Generated by irclog2html.py %(VERSION)s by <a href="mailto:marius@pov.lt">Marius Gedminas</a>
 - find it at <a href="http://mg.pov.lt/irclog2html.py">mg.pov.lt</a>!
</tt></body></html>""" % {'VERSION': VERSION, 'RELEASE': RELEASE},

    def servermsg(self, time, what, text):
        text = escape(text)
        text = createlinks(text)
        colour = self.colours.get(what)
        if colour:
            text = '<font color="%s">%s</font>' % (colour, text)
        self._servermsg(text)

    def _servermsg(self, line):
        print >> self.outfile, '%s<br>' % line

    def nicktext(self, time, nick, text, htmlcolour):
        nick = escape(nick)
        text = escape(text)
        text = createlinks(text)
        text = text.replace('  ', '&nbsp;&nbsp;')
        self._nicktext(time, nick, text, htmlcolour)

    def _nicktext(self, time, nick, text, htmlcolour):
        print >> self.outfile, '&lt;%s&gt; %s<br>' % (nick, text)


class TextStyle(SimpleTextStyle):
    """Text style using colours for each nick"""

    name = "tt"
    description = __doc__

    def _nicktext(self, time, nick, text, htmlcolour):
        print >> self.outfile, ('<font color="%s">&lt;%s&gt;</font>'
                                ' <font color="#000000">%s</font><br>'
                                % (htmlcolour, nick, text))


class SimpleTableStyle(SimpleTextStyle):
    """Table style, without heavy use of colour"""

    name = "simpletable"

    def head(self, title, prev=None, index=None, next=None,
             charset="iso-8859-1"):
        SimpleTextStyle.head(self, title, prev, index, next, charset)
        print >> self.outfile, "<table cellspacing=3 cellpadding=2 border=0>"

    def foot(self):
        print >> self.outfile, "</table>"
        SimpleTextStyle.foot(self)

    def _servermsg(self, line):
        print >> self.outfile, ('<tr><td colspan=2><tt>%s</tt></td></tr>'
                                % line)

    def _nicktext(self, time, nick, text, htmlcolour):
        print >> self.outfile, ('<tr bgcolor="#eeeeee"><th><font color="%s">'
                                '<tt>%s</tt></font></th>'
                                '<td width="100%%"><tt>%s</tt></td></tr>'
                                % (htmlcolour, nick, text))


class TableStyle(SimpleTableStyle):
    """Default style, using a table with bold colours"""

    name = "table"
    description = __doc__

    def _nicktext(self, time, nick, text, htmlcolour):
        print >> self.outfile, ('<tr><th bgcolor="%s"><font color="#ffffff">'
                                '<tt>%s</tt></font></th>'
                                '<td width="100%%" bgcolor="#eeeeee"><tt><font'
                                ' color="%s">%s</font></tt></td></tr>'
                                % (htmlcolour, nick, htmlcolour, text))


class XHTMLStyle(AbstractStyle):
    """Text style, produces XHTML that can be styled with CSS"""

    name = 'xhtml'
    description = __doc__

    CLASSMAP = {
        LogParser.ACTION: 'action',
        LogParser.JOIN: 'join',
        LogParser.PART: 'part',
        LogParser.NICKCHANGE: 'nickchange',
        LogParser.SERVER: 'servermsg',
        LogParser.OTHER: 'other',
    }

    prefix = '<div class="irclog">'
    suffix = '</div>'

    def head(self, title, prev=('', ''), index=('', ''), next=('', ''),
             charset="UTF-8"):
        print >> self.outfile, """\
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"
          "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html>
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=%(charset)s" />
  <title>%(title)s</title>
  <link rel="stylesheet" href="irclog.css" />
  <meta name="generator" content="irclog2html.py %(VERSION)s by Marius Gedminas" />
  <meta name="version" content="%(VERSION)s - %(RELEASE)s" />
</head>
<body>""" % {'VERSION': VERSION, 'RELEASE': RELEASE,
             'title': escape(title), 'charset': charset}
        self.heading(title)
        self.navbar(prev, index, next)
        print >> self.outfile, self.prefix

    def heading(self, title):
        print >> self.outfile, '<h1>%s</h1>' % escape(title)

    def link(self, url, title):
        # Intentionally not escaping title so that &entities; work
        if url:
            print >> self.outfile, ('<a href="%s">%s</a>'
                                    % (escape(urllib.quote(url)),
                                       title or escape(url))),
        elif title:
            print >> self.outfile, ('<span class="disabled">%s</span>'
                                    % title),

    def navbar(self, prev, index, next):
        prev_title, prev_url = prev
        index_title, index_url = index
        next_title, next_url = next
        if not (prev_title or index_title or next_title or
                prev_url or index_url or next_url):
            return
        print >> self.outfile, '<div class="navigation">',
        self.link(prev_url, prev_title)
        self.link(index_url, index_title)
        self.link(next_url, next_title)
        print >> self.outfile, '</div>'

    def foot(self):
        print >> self.outfile, self.suffix
        print >> self.outfile, """
<div class="generatedby">
<p>Generated by irclog2html.py %(VERSION)s by <a href="mailto:marius@pov.lt">Marius Gedminas</a>
 - find it at <a href="http://mg.pov.lt/irclog2html.py">mg.pov.lt</a>!</p>
</div>
</body>
</html>""" % {'VERSION': VERSION, 'RELEASE': RELEASE}

    def servermsg(self, time, what, text):
        """Output a generic server message.

        `time` is a string.
        `line` is not escaped.
        `what` is one of LogParser event constants (e.g. LogParser.JOIN).
        """
        text = escape(text)
        text = createlinks(text)
        if time:
            displaytime = shorttime(time)
            print >> self.outfile, ('<p id="t%s" class="%s">'
                                    '<a href="#t%s" class="time">%s</a> '
                                    '%s</p>'
                                    % (time, self.CLASSMAP[what], time,
                                       displaytime, text))
        else:
            print >> self.outfile, ('<p class="%s">%s</p>'
                                    % (self.CLASSMAP[what], text))

    def nicktext(self, time, nick, text, htmlcolour):
        """Output a comment uttered by someone.

        `time` is a string.
        `nick` and `text` are not escaped.
        `htmlcolour` is a string ('#rrggbb').
        """
        nick = escape(nick)
        text = escape(text)
        text = createlinks(text)
        text = text.replace('  ', '&nbsp;&nbsp;')
        if time:
            displaytime = shorttime(time)
            print >> self.outfile, ('<p id="t%s" class="comment">'
                                    '<a href="#t%s" class="time">%s</a> '
                                    '<span class="nick" style="color: %s">'
                                    '&lt;%s&gt;</span>'
                                    ' <span class="text">%s</span></p>'
                                    % (time, time, displaytime, htmlcolour, nick,
                                       text))
        else:
            print >> self.outfile, ('<p class="comment">'
                                    '<span class="nick" style="color: %s">'
                                    '&lt;%s&gt;</span>'
                                    ' <span class="text">%s</span></p>'
                                    % (htmlcolour, nick, text))


class XHTMLTableStyle(XHTMLStyle):
    """Table style, produces XHTML that can be styled with CSS"""

    name = 'xhtmltable'
    description = __doc__

    prefix = '<table class="irclog">'
    suffix = '</table>'

    def servermsg(self, time, what, text):
        text = escape(text)
        text = createlinks(text)
        if time:
            displaytime = shorttime(time)
            print >> self.outfile, ('<tr id="t%s">'
                                    '<td class="%s" colspan="2">%s</td>'
                                    '<td><a href="#t%s" class="time">%s</a></td>'
                                    '</tr>'
                                    % (time, self.CLASSMAP[what], text,
                                       time, displaytime))
        else:
            print >> self.outfile, ('<tr>'
                                    '<td class="%s" colspan="3">%s</td>'
                                    '</tr>'
                                    % (self.CLASSMAP[what], text))

    def nicktext(self, time, nick, text, htmlcolour):
        nick = escape(nick)
        text = escape(text)
        text = createlinks(text)
        text = text.replace('  ', '&nbsp;&nbsp;')
        if time:
            displaytime = shorttime(time)
            print >> self.outfile, ('<tr id="t%s">'
                                    '<th class="nick" style="background: %s">%s</th>'
                                    '<td class="text" style="color: %s">%s</td>'
                                    '<td class="time">'
                                    '<a href="#t%s" class="time">%s</a></td>'
                                    '</tr>'
                                    % (time, htmlcolour, nick, htmlcolour, text,
                                       time, displaytime))
        else:
            print >> self.outfile, ('<tr>'
                                    '<th class="nick" style="background: %s">%s</th>'
                                    '<td class="text" colspan="2" style="color: %s">%s</td>'
                                    '</tr>'
                                    % (htmlcolour, nick, htmlcolour, text))


#
# Main
#

# All styles
STYLES = [
    SimpleTextStyle,
    TextStyle,
    SimpleTableStyle,
    TableStyle,
    XHTMLStyle,
    XHTMLTableStyle,
]

# Customizable colours
COLOURS = [
    ("part",       "#000099", LogParser.PART),
    ("join",       "#009900", LogParser.JOIN),
    ("server",     "#009900", LogParser.SERVER),
    ("nickchange", "#009900", LogParser.NICKCHANGE),
    ("action",     "#CC00CC", LogParser.ACTION),
]


def main(argv=sys.argv):
    progname = os.path.basename(argv[0])
    parser = optparse.OptionParser("usage: %prog [options] filename",
                                   prog=progname,
                                   description="Colourises and converts IRC"
                                               " logs to HTML format for easy"
                                               " web reading.")
    parser.add_option('-s', '--style', dest="style", default="table",
                      help="format log according to specific style"
                           " (default: table); try -s help for a list of"
                           " available styles")
    parser.add_option('-t', '--title', dest="title", default=None,
                      help="title of the page (default: same as file name)")
    parser.add_option('--prev-title', dest="prev_title", default='',
                      help="title of the previous page (default: none)")
    parser.add_option('--prev-url', dest="prev_url", default='',
                      help="URL of the previous page (default: none)")
    parser.add_option('--index-title', dest="index_title", default='',
                      help="title of the index page (default: none)")
    parser.add_option('--index-url', dest="index_url", default='',
                      help="URL of the index page (default: none)")
    parser.add_option('--next-title', dest="next_title", default='',
                      help="title of the next page (default: none)")
    parser.add_option('--next-url', dest="next_url", default='',
                      help="URL of the next page (default: none)")
    for name, default, what in COLOURS:
        parser.add_option('--color-%s' % name, '--colour-%s' % name,
                          dest="colour_%s" % name, default=default,
                          help="select %s colour (default: %s)"
                               % (name, default))
    options, args = parser.parse_args(argv[1:])
    if options.style == "help":
        print "The following styles are available for use with irclog2html.py:"
        for style in STYLES:
            print
            print "  %s" % style.name
            print "    %s" % style.description
        print
        return
    for style in STYLES:
        if style.name == options.style:
            break
    else:
        parser.error("unknown style: %s" % style)
    colours = {}
    for name, default, what in COLOURS:
        colours[what] = getattr(options, 'colour_%s' % name)
    if not args:
        parser.error("required parameter missing")
    title = options.title
    prev = (options.prev_title, options.prev_url)
    index = (options.index_title, options.index_url)
    next = (options.next_title, options.next_url)

    for filename in args:
        try:
            infile = open(filename)
        except EnvironmentError, e:
            sys.exit("%s: cannot open %s for reading: %s"
                     % (progname, filename, e))
        outfilename = filename + ".html"
        try:
            outfile = open(outfilename, "w")
        except EnvironmentError, e:
            infile.close()
            sys.exit("%s: cannot open %s for writing: %s"
                     % (progname, outfilename, e))
        try:
            parser = LogParser(infile)
            formatter = style(outfile, colours)
            convert_irc_log(parser, formatter, title or filename,
                            prev, index, next)
        finally:
            outfile.close()
            infile.close()


def convert_irc_log(parser, formatter, title, prev, index, next):
    """Convert IRC log to HTML or some other format."""
    nick_colour = NickColourizer()
    formatter.head(title, prev, index, next)
    for time, what, info in parser:
        if what == LogParser.COMMENT:
            nick, text = info
            htmlcolour = nick_colour[nick]
            formatter.nicktext(time, nick, text, htmlcolour)
        else:
            if what == LogParser.NICKCHANGE:
                text, oldnick, newnick = info
                nick_colour.change(oldnick, newnick)
            else:
                text = info
            formatter.servermsg(time, what, text)
    formatter.foot()


if __name__ == '__main__':
    main()
