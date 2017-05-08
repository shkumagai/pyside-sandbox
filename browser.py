# -*- coding: utf-8 -*-
import datetime
import sys

from argparse import ArgumentParser
from PySide.QtCore import QUrl, QTimer
from PySide.QtGui import QApplication, QImage, QPainter
from PySide.QtNetwork import QNetworkRequest
from PySide.QtWebKit import QWebView, QWebPage


class Page(QWebPage):
    """psedo webpage class
    """
    def __init__(self, ua):
        QWebPage.__init__(self)
        self.ua = ua

    def userAgentForUrl(self, url):
        """override 'userAgentforurl' method
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

        self.timerScreen = QTimer()
        self.timerScreen.setInterval(1000)
        self.timerScreen.setSingleShot(True)
        self.timerScreen.timeout.connect(self._load_finished)

        self.loadFinished.connect(self.timerScreen.start)

    def _load_finished(self):
        """Callback function when content loading finished
        """
        frame = self.page().mainFrame()
        size = frame.contentsSize()
        self.page().setViewportSize(size)
        self.scroll(0, size.height())
        frame.scroll(0, size.height())
        image = QImage(size, QImage.Format_ARGB32)
        painter = QPainter(image)

        frame.render(painter)
        painter.end()

        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        file_name = "{}_{}.png".format(args.prefix, timestamp)
        image.save(file_name)
        print("page title: [{}] --> save as {}".format(self.title(), file_name))
        sys.exit()

    def run(self, args):
        """prepare request object, then call 'load' method of QWebView object
        """
        print(args)
        request = QNetworkRequest()
        request.setUrl(QUrl(args.url))
        request.setRawHeader("Accept-Languages", ', '.join(args.language))
        request.setRawHeader("User-Agent", args.agent)

        self.resize(int(args.width), int(args.height))
        self.load(request)


def main(args):
    """main function
    """
    app = QApplication(sys.argv)
    page = Page(args.agent) if args.agent else None
    browser = Browser(page)
    browser.run(args)
    browser.show()
    app.exec_()


if __name__ == "__main__":
    """
    """
    ap = ArgumentParser()
    ap.add_argument('-a', '--agent', default=None,
                    help="UA strings for HTTP Header 'User-Agent'")
    ap.add_argument('-l', '--language', default=['ja', 'en'],
                    help="")
    ap.add_argument('-u', '--url', default='http://shkumagai.github.io/blog/pages/about.html',
                    help="default request url (dummy)")
    ap.add_argument('-w', '--width', default=1024,
                    help="specify window width to capture screen")
    ap.add_argument('-H', '--height', default=768,
                    help="specify minimum window height to capture screen")
    ap.add_argument('-p', '--prefix', default='screenshot',
                    help="specify PNG file prefix (timestamp follows)")
    args = ap.parse_args()

    main(args)
