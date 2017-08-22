#!/usr/bin/env python
import logging

import ghost


def main(url, output_path):
    g = ghost.Ghost()
    with g.start(display=True, viewport_size=(1366, 800)) as session:
        res = session.open(url, timeout=30)
        print(res)


if __name__ == '__main__':
    url = 'http://www.google.com/'
    output_path = 'capture_0.png'

    logger = logging.getLogger('script')
    sh = logging.StreamHandler()
    sh.setLevel(logging.DEBUG)
    ghost.logger = logger

    main(url, output_path)

