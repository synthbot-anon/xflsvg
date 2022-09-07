from ubuntu:latest

# Make sure installation scripts don't ask for input
ENV DEBIAN_FRONTEND=noninteractive


# Install basic packages
RUN apt-get update && \
  apt-get install -y python3 python3-pip python3-setuptools git curl && \
  pip install -U pip && \
  pip install wheel

RUN apt-get install -y libmagickwand-dev
RUN apt clean

# Install ImageMagick
RUN git clone https://github.com/ImageMagick/ImageMagick.git /magick
WORKDIR /magick
RUN ./configure \
    --with-quantum-depth=8 \
    --without-perl \
    --without-magick-plus-plus \
    --with-rsvg=yes && \
  make -j$(nproc) && make install && ldconfig /usr/local/lib  && \
  rm -r /magick

# Set up user celestia
RUN adduser --disabled-password --gecos "" celestia
USER celestia

# Install xflsvg
RUN pip install virtualenv maturin && \
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh /dev/stdin -y
ENV PATH="/home/celestia/.cargo/bin:$PATH"

WORKDIR /home/celestia

RUN python3 -m virtualenv .venv && \
  .venv/bin/pip install maturin
RUN .venv/bin/pip wheel 'xflsvg@git+https://github.com/synthbot-anon/xflsvg.git' && \
  pip install --no-deps --force-reinstall *.whl && \
  rm -rf .venv && \
  rm *.whl

RUN rm -rf .cargo .rustup .cache/* .local/share/virtualenv/

WORKDIR /host
ENTRYPOINT ["python3", "-m", "xflsvg"]
