# -*- coding: utf-8 -*-
"""Web screen capture script with QtWebKit

How to use
==========

  $ python screenshot.py -h
  usage: screenshot.py [-h] [-a AGENT] [-l LANGUAGE] [-w WIDTH] [-H HEIGHT]
                       [-p PREFIX] [-s]
                       url

  positional arguments:
    url                   specify request url

  optional arguments:
    -h, --help            show this help message and exit
    -a AGENT, --agent AGENT
                          UA strings for HTTP Header 'User-Agent'
    -l LANGUAGE, --language LANGUAGE
                          specify langs for HTTP Header 'Accept-Language'
    -w WIDTH, --width WIDTH
                          specify window width to capture screen
    -H HEIGHT, --height HEIGHT
                          specify minimum window height to capture screen
    -p PREFIX, --prefix PREFIX
                          specify PNG file prefix (timestamp follows)
    -s, --with-smooth-scroll
                          whether scroll down to bottom when capture the page or
                          not

"""
import datetime
import sys

from argparse import ArgumentParser


try:
    from PySide.QtCore import QUrl, QTimer, Qt
    from PySide.QtGui import QApplication, QImage, QPainter
    from PySide.QtNetwork import QNetworkRequest
    from PySide.QtWebKit import QWebView, QWebPage, QWebSettings

except ImportError:
    # Use PyQt5 when it couldn't have found PySide modules
    from PyQt5.QtCore import QUrl, QTimer, Qt
    from PyQt5.QtGui import QImage, QPainter
    from PyQt5.QtNetwork import QNetworkRequest
    from PyQt5.QtWebKit import QWebSettings
    from PyQt5.QtWebKitWidgets import QWebView, QWebPage
    from PyQt5.QtWidgets import QApplication


DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 768
DEFAULT_USERAGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_5)'
                     ' AppleWebKit/537.36 (KHTML, like Gecko)'
                     ' CDP/47.0.2526.73 Safari/537.36')
DEFAULT_PREFIX = 'screenshot'


class Page(QWebPage):
    """psedo webpage class
    """
    def __init__(self, ua):
        QWebPage.__init__(self)
        self.ua = ua

    def userAgentForUrl(self, url):
        """override 'userAgentForUrl' method
        """
        return self.ua


class Browser(QWebView):
    """psedo browser class
    """
    def __init__(self, page=None):
        """Initialize browser class
        """
        QWebView.__init__(self)
        if page:
            self.setPage(page)

        self.use_smooth_scroll = args.with_smooth_scroll
        self.scrollStarted = False
        self.initialize()

    def _private_browse(self):
        print("Enable private browsing mode")
        self.settings().setAttribute(QWebSettings.PrivateBrowsingEnabled, True)

    def _hide_scroll_bars(self):
        print("Disable scroll bars")
        self.page().mainFrame().setScrollBarPolicy(Qt.Horizontal, Qt.ScrollBarAlwaysOff)

    def initialize(self):
        self.timerDelay = QTimer()
        self.timerDelay.setInterval(50)
        self.timerDelay.setSingleShot(True)
        self.timerDelay.timeout.connect(self.delay_action)

        self.loadFinished.connect(self.load_finished_slot)
        self.loadProgress.connect(self.load_progress_slot)

        self._private_browse()
        self._hide_scroll_bars()

    def load_progress_slot(self, progress):
        """Callback function when content loading status updated.
        """
        print("Loading progress: {:d}%...".format(progress))

    def load_finished_slot(self, ok):
        """Callback function when content loading finished
        """
        if not ok:
            print("Loaded but not completed: {}".format(self.url))
            return
        print("Load completed: {}".format(self.url))
        print("Loaded content size: {:,d} x {:,d}".format(
            self.page().mainFrame().contentsSize().width(),
            self.page().mainFrame().contentsSize().height(),
        ))
        self.delay_action()

    def delay_action(self):
        frame = self.page().mainFrame()
        target_y = frame.scrollBarMaximum(Qt.Vertical)
        current_y = frame.scrollBarValue(Qt.Vertical)
        print("target: {:d}, current: {:d}".format(target_y, current_y))

        if self.use_smooth_scroll:
            y = current_y - 50 if self.scrollStarted else target_y
            if y > 0:
                frame.evaluateJavaScript("window.scrollTo(0, {:d});".format(y))
                print("Scroll to y: {:,d}".format(y))
                if not self.scrollStarted:
                    self.scrollStarted = True
                self.timerDelay.start()
            else:
                self.take_screenshot()
        else:
            self.take_screenshot()

    def take_screenshot(self):
        frame = self.page().mainFrame()
        size = frame.contentsSize()
        self.page().setViewportSize(size)

        image = QImage(size, QImage.Format_ARGB32)
        painter = QPainter(image)

        frame.render(painter)
        painter.end()

        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        file_name = "{}_{}.png".format(args.prefix, timestamp)
        print("page title: [{:s}] --> save as {:s}".format(self.title(), file_name))
        image.save(file_name)
        sys.exit()

    def run(self, args):
        """prepare request object, then call 'load' method of QWebView object
        """
        request = QNetworkRequest()
        request.setUrl(QUrl(args.url))
        request.setRawHeader(bytes("Accept-Languages", 'utf-8'), bytes(', '.join(args.language), 'utf-8'))
        request.setRawHeader(bytes("User-Agent", 'utf-8'), bytes(args.agent, 'utf-8'))

        self.resize(int(args.width) + 15, int(args.height))
        self.load(request)


def main(args):
    """main function
    """
    print(args)
    app = QApplication(sys.argv)
    page = Page(args.agent) if args.agent else None
    browser = Browser(page)
    browser.run(args)
    browser.show()
    app.exec_()


if __name__ == "__main__":
    ap = ArgumentParser()
    ap.add_argument('-a', '--agent', default=DEFAULT_USERAGENT,
                    help="UA strings for HTTP Header 'User-Agent'")
    ap.add_argument('-l', '--language', action="append",
                    help="specify langs for HTTP Header 'Accept-Language'")
    ap.add_argument('-w', '--width', default=DEFAULT_WIDTH,
                    help="specify window width to capture screen")
    ap.add_argument('-H', '--height', default=DEFAULT_HEIGHT,
                    help="specify minimum window height to capture screen")
    ap.add_argument('-p', '--prefix', default=DEFAULT_PREFIX,
                    help="specify PNG file prefix (timestamp follows)")
    ap.add_argument('-s', '--with-smooth-scroll', default=False, action="store_true",
                    help="whether scroll down to bottom when capture the page or not")
    ap.add_argument('url', help="specify request url")
    args = ap.parse_args()

    if not args.language:
        args.language = ['ja']
    main(args)
