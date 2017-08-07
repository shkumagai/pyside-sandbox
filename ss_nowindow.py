# -*- coding: utf-8 -*-
import logging
logger = logging.getLogger(__name__)

import sys
import time

from PyQt5.QtCore import Qt, QSize, QTimer, QUrl
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtNetwork import QNetworkRequest
from PyQt5.QtWebKitWidgets import QWebPage
from PyQt5.QtWidgets import QApplication



class WebKitShooter(QWebPage):
    def __init__(
        self, url, width=800, height=600, wait_time=1,
        user_agent=DEFAULT_USERAGENT,
        accept_languages='en,ja',
        cookies=None,
        referer=None,
    ):
        super(QWebPage, self).__init__()
        self.url = url
        self.width = width
        self.height = height
        self.wait_time = wait_time
        self.user_agent = user_agent
        self.accept_languages = accept_languages
        self.cookies = cookies
        self.referer = referer

        self._initialize()

    def _initialize(self):
        logger.info("Set Viewport: W:{} x H:{}".format(self.width, self.height))
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
        logger.info("load progress: {:d}%".format(progress))

    def load_finished_slot(self, ok):
        if not ok:
            logger.info("Loaded, but not completed: {}".format(self.url))
            return
        else:
            logger.info("Load completed: {}".format(self.url))
            self.loadCompleted = True

        if self.loadCompleted and self.initialLayoutFinished:
            logger.info(
                "Load completed: {}, Initial layout finished: {}".format(
                    self.loadCompleted,
                    self.initialLayoutFinished,
                )
            )
            logger.info(
                "Loaded size: W:{} x H:{}".format(
                    self.mainFrame().contentsSize().width(),
                    self.mainFrame().contentsSize().height(),
                )
            )
            self.render_and_capture(self)

    def initial_layout_slot(self):
        logger.debug("Layouted: {}".format(self.url))
        self.initialLayoutFinished = True

        if self.loadCompleted and self.initialLayoutFinished:
            logger.debug("Capture from layout: {}".format(self.url))
            logger.debug(
                "Loaded size: W:{} x H:{}".format(
                    self.mainFrame().contentsSize().width,
                    self.mainFrame().contentsSize().height,
                )
            )
            self.render_and_capture()

    def _ssl_errors_slot(self, reply, errors):
        logger.debug("SSL error occured: {}".format(errors))
        reply.ignoreSslErrors()

    def _set_props_to_network_access_manager(self):
        network_access_manager = WebKitShooterNetworkManager()
        self.setNetworkAccessManager(network_access_manager)

        if self.accept_languages:
            network_access_manager.set_accept_languages(self.accept_languages)

        if self.referer:
            network_access_manager.set_referer(self.referer)

        self.userAgentForUrl = UserAgent(self.user_agent)

        if self.cookies:
            network_access_manager.setCookieJar(generate_cookie(self.url, self.cookies))

        network_access_manager.sslErrors.connect(self._ssl_errors_slot)

    def _remove_scroll_bars(self):
        logger.info("Disable scroll bar(s)")
        self.mainFrame().setScrollBarPolicy(
            Qt.Vertical, Qt.ScrollBarAlwaysOff
        )

    def _set_fontfamily(self):
        logger.info("Set fontfamily: {}".format(FONT_FAMILY_NAME))
        self.settings().setFontFamily(
            QWebSettings.StandardFont,
            FONT_FAMILY_NAME,
        )
        self.settings().setFontFamily(
            QWebSettings.FixedFont,
            FONT_FAMILY_NAME,
        )
        self.settings().setFontFamily(
            QWebSettings.SerifFont,
            FONT_FAMILY_NAME,
        )

    def _set_private_browse(self):
        logger.info("Enable private browsing mode")
        self.settings().setAttribute(QWebSettings.PrivateBrowsingEnabled, True)

    def run(self):
        logger.info("Take a screen capture: {}".format(self.url))
        self.mainFrame().load(QUrl(self.url))

    def render_and_capture(self):
        logger.info("Render: {}".format(self.url))
        self.setViewportSize(
            QSize(
                self.width,
                self.mainFrame().contentsSize().height(),
            )
        )
        image = QImage(self.viewportSize(), QImage.Format_ARGB32)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRedderHint(QPainter.Angialiasing)
        painter.setRenderHint(QPainter.TextAngialiasing)
        painter.setRenderHint(QPainter.HighQualityAntialiasing)

        self.mainFrame().render(painter)
        painter.end()

        logger.info("Save to buffer: {}".format(self.url))


def shoot(url, width, height, user_agent, accept_languages, cookies=None, referer=None):
    qapp = QApplication(sys.argv)

    shooter = WebKitShooter()
    shooter.run()

    while not shooter.finished:
        qapp.processEvents()
        time.sleep(0.01)
    shooter = None
