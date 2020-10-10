#!/bin/bash

function parse_yaml {
   local prefix=$2
   local s='[[:space:]]*' w='[a-zA-Z0-9_]*' fs=$(echo @|tr @ '\034')
   sed -ne "s|^\($s\):|\1|" \
        -e "s|^\($s\)\($w\)$s:$s[\"']\(.*\)[\"']$s\$|\1$fs\2$fs\3|p" \
        -e "s|^\($s\)\($w\)$s:$s\(.*\)$s\$|\1$fs\2$fs\3|p"  $1 |
   awk -F$fs '{
      indent = length($1)/2;
      vname[indent] = $2;
      for (i in vname) {if (i > indent) {delete vname[i]}}
      if (length($3) > 0) {
         vn=""; for (i=0; i<indent; i++) {vn=(vn)(vname[i])("_")}
         printf("%s%s%s=\"%s\"\n", "'$prefix'",vn, $2, $3);
      }
   }'
}

CONFIG_FILE=/etc/molior/molior.yml

# Reads the config yaml and sets env variables
eval $(parse_yaml $CONFIG_FILE)
APTLY=$aptly__apt_url
APTLY_KEY=$aptly__key

if [ "$1" != "info" -a "$#" -lt 5 ]; then
  echo "Usage: $0 build|publish|remove <distrelease> <name> <version> <architecture> [components,]" >&2
  echo "       $0 info" 1>&2
  exit 1
fi

ACTION=$1
DIST_RELEASE=$2
DIST_NAME=$3
DIST_VERSION=$4
ARCH=$5
COMPONENTS=$6
REPO_URL=$7
KEYS=$8

DEBOOTSTRAP_NAME="${DIST_NAME}_${DIST_VERSION}_$ARCH"
DEBOOTSTRAP="/var/lib/molior/debootstrap/$DEBOOTSTRAP_NAME"

# Workaround obsolete pxz package on buster
xzversion=`dpkg -s xz-utils | grep ^Version: | sed 's/^Version: //'`
if dpkg --compare-versions "$xzversion" lt 5.2.4-1; then
  TAR_PXZ="-Ipxz"
else
  TAR_PXZ=""
fi

set -e

build_debootstrap()
{
  target=$DEBOOTSTRAP

  if [ -d $target ]; then
    rm -rf $target
  fi

  echo
  message="Creating debootstrap $DEBOOTSTRAP_NAME"
  echo "++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
  printf "| %-44s %s |\n" "$message" "`date -R`"
  echo "++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
  echo

  echo " * running debootstrap for $DIST_NAME/$DIST_VERSION $ARCH"

  echo I: Using APT repository $REPO_URL

  if [ -n "$COMPONENTS" ]; then
      COMPONENTS="--components main,$COMPONENTS"
  fi
  INCLUDE="--include=gnupg1"

  if echo $KEYS | grep -q '#'; then
      keyserver=`echo $KEYS | cut -d# -f1`
      keyids=`echo $KEYS | cut -d# -f2 | tr ',' ' '`
      echo I: Downloading gpg public key: $keyserver $keyids
      flock /root/.gnupg.molior gpg1 --no-default-keyring --keyring=trustedkeys.gpg --keyserver $keyserver --recv-keys $keyids
  else
      echo I: Downloading gpg public key: $KEYS
      keyfile=`mktemp /tmp/molior-repo.asc.XXXXXX`
      wget -q $KEYS -O $keyfile
      cat $keyfile | flock /root/.gnupg.molior gpg1 --import --no-default-keyring --keyring=trustedkeys.gpg
  fi

  if echo $ARCH | grep -q arm; then
    debootstrap --foreign --arch $ARCH --keyring=/root/.gnupg/trustedkeys.gpg --variant=minbase $INCLUDE $COMPONENTS $DIST_RELEASE $target $REPO_URL
    if [ $? -ne 0 ]; then
      echo "debootstrap failed"
      exit 1
    fi
    if [ "$ARCH" = "armhf" ]; then
      cp /usr/bin/qemu-arm-static $target/usr/bin/
    else
      cp /usr/bin/qemu-aarch64-static $target/usr/bin/
    fi
    chroot $target /debootstrap/debootstrap --second-stage --no-check-gpg
    if [ $? -ne 0 ]; then
      echo "debootstrap failed"
      exit 2
    fi
  else
    debootstrap --arch $ARCH --keyring=/root/.gnupg/trustedkeys.gpg --variant=minbase $INCLUDE $COMPONENTS $DIST_RELEASE $target $REPO_URL
    if [ $? -ne 0 ]; then
      echo "debootstrap failed"
      exit 3
    fi
  fi

  echo I: Configuring debootstrap
  if chroot $target dpkg -s > /dev/null 2>&1; then
    # The package tzdata cannot be --excluded in debootstrap, so remove it here
    # In order to use debconf for configuring the timezone, the tzdata package
    # needs to be installed later as a dependency, i.e. after the config package
    # preseeding debconf.
    chroot $target apt-get purge --yes tzdata
    rm -f $target/etc/timezone
  fi

  chroot $target apt-get clean

  rm -f $target/var/lib/apt/lists/*Packages* $target/var/lib/apt/lists/*Release*

  echo I: Created debootstrap successfully
}

publish_debootstrap()
{
  rm -f $DEBOOTSTRAP.tar.xz

  echo I: Creating debootstrap tar
  cd $DEBOOTSTRAP
  tar $TAR_PXZ -cf ../$DEBOOTSTRAP_NAME.tar.xz .
  cd - > /dev/null
  rm -rf $DEBOOTSTRAP

  echo I: debootstrap $DEBOOTSTRAP is ready
}

case "$ACTION" in
  info)
    echo "debootstrap minimal rootfs"
    ;;
  build)
    build_debootstrap
    ;;
  publish)
    publish_debootstrap
    ;;
  remove)
    rm -rf $DEBOOTSTRAP $DEBOOTSTRAP.tar.xz
    ;;
  *)
    echo "Unknown action $ACTION"
    exit 1
    ;;
esac

