# -*- coding: utf-8 -*-
"""Web screen capture script with QtWebKit."""
import datetime
import logging
import sys
import time

from urllib.parse import quote_plus, urlparse

from PyQt5.QtCore import QSize, QTimer, QUrl, Qt
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtNetwork import (
    QNetworkAccessManager,
    QNetworkCookie,
    QNetworkCookieJar,
)
from PyQt5.QtWebKitWidgets import QWebPage
from PyQt5.QtWebKit import QWebSettings
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger(__name__)

FONT_FAMILY_NAME = 'Noto Sans CJK JP'
DEFAULT_USERAGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_5)'
                     ' AppleWebKit/537.36 (KHTML, like Gecko)'
                     ' CDP/47.0.2526.73 Safari/537.36')


def generate_cookie(url, cookies):
    logger.info("Generate Cookies: {0} {1}".format(url, cookies))
    if not cookies:
        return QNetworkCookieJar()

    qcookies = []
    res = urlparse(url)
    secure = True if res.scheme == 'https' else False

    for cookie in cookies:
        qcookie = QNetworkCookie(cookie.name, cookie.value)
        if secure:
            qcookie.setSecure(True)
        qcookies.append(qcookie)

    qcookiejar = QNetworkCookieJar()
    qcookiejar.setCookieFromUrl(qcookies, QUrl(url))
    return qcookiejar


class UserAgent(object):
    """UserAgent for WebKitshooter."""

    def __init__(self, user_agent):
        """Initialize."""
        logger.info("Set User-Agent: {0}".format(user_agent))
        self.user_agent = user_agent

    def __call__(self, url):
        """Return 'User-Agent' value."""
        return self.user_agent


class WebKitShooterNetworkManager(QNetworkAccessManager):
    """NetworkAccessManager for WebKitShooter."""

    def set_accept_languages(self, accept_languages):
        """Handle 'Accept-Languages' value."""
        logger.info("Set Accept-Languages: {0}".format(accept_languages))
        self.accept_languages = accept_languages

    def set_referer(self, referer):
        """Handle 'Referer' value."""
        logger.info("Set Referer: {0}".format(referer))
        self.referer = referer

    def createRequest(self, op, req, outgoing_data):  # noqa: N802
        """Create request object with RawHeader values."""
        req.setRawHeader(
            bytes('Cache-Control', 'utf-8'),
            bytes('no-cache', 'utf-8'),
        )

        if hasattr(self, 'accept_languages'):
            req.setRawHeader(
                bytes('Accept-Languages', 'utf-8'),
                bytes(quote_plus(self.accept_languages), 'utf-8'),
            )

        if hasattr(self, 'referer'):
            req.setRawHeader(
                bytes('Referer', 'utf-8'),
                bytes(quote_plus(self.referer), 'utf-8'),
            )

        return super().createRequest(op, req, outgoing_data)


class WebKitShooter(QWebPage):
    """Psedo webpage class."""

    def __init__(
        self, url, width=800, height=600, wait_time=1,
        user_agent=DEFAULT_USERAGENT,
        accept_languages='en,ja',
        cookies=None,
        referer=None,
    ):
        """Initialize."""
        super(QWebPage, self).__init__()

        # attributes
        self.url = url
        self.width = width
        self.height = height
        self.wait_time = wait_time
        self.user_agent = user_agent
        self.accept_languages = accept_languages
        self.cookies = cookies
        self.referer = referer

        # flags
        self.loadCompleted = False
        self.initialLayoutFinished = False
        self.finished = False

        self._initialize()

    def _initialize(self):
        """Initialize additional settings."""
        logger.info(
            "Set Viewport: W:{0} x H:{1}".format(self.width, self.height),
        )
        self.setViewportSize(QSize(self.width, self.height))

        self.loadProgress.connect(self.load_progress_slot)
        self.loadFinished.connect(self.load_finished_slot)
        self.mainFrame().initialLayoutCompleted.connect(
            self.initial_layout_slot,
        )

        self._set_fontfamily()
        self._set_props_to_network_access_manager()
        self._remove_scroll_bars()
        self._set_private_browse()

    def load_progress_slot(self, progress):
        """Print prgress message when content loading in progress."""
        logger.info("load progress: {0:d}%".format(progress))

    def load_finished_slot(self, ok):
        """Dispatch capture task when content loading finished."""
        if not ok:
            logger.info("Loaded, but not completed: {0}".format(self.url))
            return
        else:
            logger.info("Load completed: {0}".format(self.url))
            self.loadCompleted = True

        if self.loadCompleted and self.initialLayoutFinished:
            logger.info(
                "Load completed: {0}, Initial layout finished: {1}".format(
                    self.loadCompleted,
                    self.initialLayoutFinished,
                ),
            )
            logger.info(
                "Loaded size: W:{0} x H:{1}".format(
                    self.mainFrame().contentsSize().width(),
                    self.mainFrame().contentsSize().height(),
                ),
            )
            self.render_and_capture(self)

    def initial_layout_slot(self):
        """Dispatch capture task when initial layout setting finished."""
        logger.debug("Layouted: {0}".format(self.url))
        self.initialLayoutFinished = True

        if self.loadCompleted and self.initialLayoutFinished:
            logger.debug("Capture from layout: {0}".format(self.url))
            logger.debug(
                "Loaded size: W:{0} x H:{1}".format(
                    self.mainFrame().contentsSize().width,
                    self.mainFrame().contentsSize().height,
                ),
            )
            self.render_and_capture()

    def _ssl_errors_slot(self, reply, errors):
        """Print error messages when SSL error occured."""
        logger.debug("SSL error occured: {0}".format(errors))
        reply.ignoreSslErrors()

    def _set_props_to_network_access_manager(self):
        """Initialize NetworkManager."""
        network_access_manager = WebKitShooterNetworkManager()
        self.setNetworkAccessManager(network_access_manager)

        if self.accept_languages:
            network_access_manager.set_accept_languages(self.accept_languages)

        if self.referer:
            network_access_manager.set_referer(self.referer)

        self.userAgentForUrl = UserAgent(self.user_agent)

        if self.cookies:
            network_access_manager.setCookieJar(
                generate_cookie(self.url, self.cookies),
            )

        network_access_manager.sslErrors.connect(self._ssl_errors_slot)

    def _remove_scroll_bars(self):
        """Set up ScrollBar Policy."""
        logger.info("Disable scroll bar(s)")
        self.mainFrame().setScrollBarPolicy(
            Qt.Vertical, Qt.ScrollBarAlwaysOff,
        )

    def _set_fontfamily(self):
        """Set up font-family."""
        logger.info("Set fontfamily: {0}".format(FONT_FAMILY_NAME))
        self.settings().setFontFamily(
            QWebSettings.StandardFont, FONT_FAMILY_NAME,
        )
        self.settings().setFontFamily(
            QWebSettings.FixedFont, FONT_FAMILY_NAME,
        )
        self.settings().setFontFamily(
            QWebSettings.SerifFont, FONT_FAMILY_NAME,
        )

    def _set_private_browse(self):
        """Set up Private browsing mode."""
        logger.info("Enable private browsing mode")
        self.settings().setAttribute(QWebSettings.PrivateBrowsingEnabled, True)

    def run(self):
        """Dispatch screen capture task."""
        logger.info("Take a screen capture: {0}".format(self.url))
        self.mainFrame().load(QUrl(self.url))

    def render_and_capture(self):
        """Render content and save capture into image file."""
        logger.info("Render: {0}".format(self.url))
        self.setViewportSize(
            QSize(
                self.width,
                self.mainFrame().contentsSize().height(),
            ),
        )
        image = QImage(self.viewportSize(), QImage.Format_ARGB32)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRedderHint(QPainter.Angialiasing)
        painter.setRenderHint(QPainter.TextAngialiasing)
        painter.setRenderHint(QPainter.HighQualityAntialiasing)

        self.mainFrame().render(painter)
        painter.end()

        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        file_name = "{0}_{1}.png".format(self.prefix, timestamp)
        logger.info(
            "Page title: [{0:s}] --> save as {1:s}".format(
                self.title(),
                file_name,
            ),
        )
        image.save(file_name)
        self.finished = True


def shoot(
        url, width, height, user_agent, accept_languages,
        cookies=None, referer=None, ):
    """Take screenshot."""
    qapp = QApplication(sys.argv)

    shooter = WebKitShooter(url,
                            width=1366,
                            height=600,
                            user_agent=DEFUALT_USERAGENT,
                            accept_languages='en,ja',
    )
    shooter.run()

    while not shooter.finished:
        qapp.processEvents()
        time.sleep(0.01)
    shooter = None
