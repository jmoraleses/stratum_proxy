#!/bin/bash

# Detectar arquitectura del sistema
ARCH=$(uname -m)
if [ "$ARCH" == "x86_64" ]; then
    BITCOIN_CORE_VERSION="24.0"  # Cambia esto a la versión más reciente si es necesario
    BITCOIN_CORE_URL="https://bitcoincore.org/bin/bitcoin-core-$BITCOIN_CORE_VERSION/bitcoin-$BITCOIN_CORE_VERSION-x86_64-linux-gnu.tar.gz"
elif [ "$ARCH" == "aarch64" ]; then
    BITCOIN_CORE_VERSION="24.0"  # Cambia esto a la versión más reciente si es necesario
    BITCOIN_CORE_URL="https://bitcoincore.org/bin/bitcoin-core-$BITCOIN_CORE_VERSION/bitcoin-$BITCOIN_CORE_VERSION-aarch64-linux-gnu.tar.gz"
else
    echo "Arquitectura no compatible: $ARCH"
    exit 1
fi

DATA_DIR="$HOME/.bitcoin"
CONFIG_FILE="$DATA_DIR/bitcoin.conf"
BOOTSTRAP_BASE_URL="https://github.com/Blockchains-Download/Bitcoin/releases/download/2024.05.01/Bitcoin-Blockchain-2024-05-01.7z."
BOOTSTRAP_FILES=("001" "002" "003" "004")

# Actualizar el sistema y instalar dependencias
sudo apt-get update
sudo apt-get install -y wget tar jq bc p7zip-full

# Crear directorio de datos de Bitcoin
mkdir -p $DATA_DIR

# Descargar e instalar Bitcoin Core
cd /tmp
wget $BITCOIN_CORE_URL
tar -xzvf bitcoin-$BITCOIN_CORE_VERSION-*-linux-gnu.tar.gz
sudo install -m 0755 -o root -g root -t /usr/local/bin bitcoin-$BITCOIN_CORE_VERSION/bin/*

# Descargar y extraer los archivos de blockchain
echo "Descargando archivos de blockchain..."
cd $DATA_DIR
for part in "${BOOTSTRAP_FILES[@]}"; do
    wget "${BOOTSTRAP_BASE_URL}${part}"
done
cat Bitcoin-Blockchain-2024-05-01.7z.* > Bitcoin-Blockchain-2024-05-01.7z
7z x Bitcoin-Blockchain-2024-05-01.7z -o$DATA_DIR

# Configurar Bitcoin Core
echo "Configurando Bitcoin Core en modo de recorte..."
cat << EOF > $CONFIG_FILE
rpcuser=userbit
rpcpassword=passbit
rpcallowip=127.0.0.1
rpcallowip=192.168.1.0/24
rpcallowip=172.16.0.0/12
server=1
daemon=1
rpcbind=0.0.0.0
# Incrementar el número máximo de conexiones RPC permitidas
rpcthreads=16
# Incrementar el número máximo de conexiones de clientes
maxconnections=4
# ZeroMQ
zmqpubrawblock=tcp://*:3000
zmqpubrawtx=tcp://*:3001
EOF

# Iniciar Bitcoin Core
echo "Iniciando Bitcoin Core..."
bitcoind -daemon

# Función para comprobar el estado de sincronización
check_sync_status() {
    local progress
    progress=$(bitcoin-cli -rpcuser=tuusuario -rpcpassword=tupassword getblockchaininfo | jq -r '.verificationprogress')
    echo "Progreso de sincronización: $(echo "$progress * 100" | bc)%"
}

# Esperar a que Bitcoin Core se sincronice completamente
echo "Esperando a que Bitcoin Core se sincronice completamente..."
while : ; do
    sync_status=$(bitcoin-cli -rpcuser=tuusuario -rpcpassword=tupassword getblockchaininfo | jq -r '.initialblockdownload')
    if [ "$sync_status" == "false" ]; then
        echo "Bitcoin Core se ha sincronizado completamente."
        break
    else
        check_sync_status
        sleep 60
    fi
done

echo "Instalación y configuración de Bitcoin Core completa. Bitcoin Core se ha sincronizado en modo de recorte."
