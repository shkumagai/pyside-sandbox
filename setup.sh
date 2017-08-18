#!/bin/bash

ARCH_NAME="PyQt5_gpl-5.8.2"

curl -L https://downloads.sourceforge.net/project/pyqt/PyQt5/PyQt-5.8.2/${ARCH_NAME}.tar.gz | tar xvz
cd ${ARCH_NAME} \
    && python configure.py \
              --confirm-license \
              --disable=QtPositioning \
              --sip-incdir=/usr/local/include \
    && make && make install
cd .. \
    && rm -rf ${ARCH_NAME}
