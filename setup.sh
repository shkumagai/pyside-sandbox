#!/bin/bash

ARCH_NAME="PyQt5_gpl-5.8.2"

curl -L https://downloads.sourceforge.net/project/pyqt/PyQt5/PyQt-5.8.2/${ARCH_NAME}.tar.gz | tar xvz
cd PyQt5_gpl-5.8.2 \
    && python configure.py \
              --confirm-license \
              --disable=QtPositioning \
              --sip-incdir=/usr/local/include \
    && make && make install
rm -rf ${ARCH_NAME}
