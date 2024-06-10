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

HOME="/home/ubuntu"
DATA_DIR="$HOME/.bitcoin"
CONFIG_FILE="$DATA_DIR/bitcoin.conf"
BOOTSTRAP_BASE_URL="https://github.com/Blockchains-Download/Bitcoin/releases/download/2024.05.01/Bitcoin-Blockchain-2024-05-01.7z."
BOOTSTRAP_FILES=("001" "002" "003" "004")

# Actualizar el sistema y instalar dependencias
sudo apt-get update
sudo apt-get install -y wget tar jq bc p7zip-full

# Detener Bitcoin Core si está ejecutándose
bitcoin-cli stop

# Eliminar Bitcoin Core y sus archivos
sudo rm -rf /usr/local/bin/bitcoin*
rm -rf $DATA_DIR

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

# Eliminar archivos de bootstrap después de la extracción
echo "Eliminando archivos de bootstrap..."
rm -f Bitcoin-Blockchain-2024-05-01.7z.*
rm -f Bitcoin-Blockchain-2024-05-01.7z

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
prune=550
EOF

# Iniciar Bitcoin Core
echo "Iniciando Bitcoin Core..."
bitcoind -daemon

# Esperar unos segundos para que bitcoind inicie
sleep 10


# Función para comprobar el estado de sincronización
check_sync_status() {
    local progress
    progress=$(bitcoin-cli -rpcuser=$RPC_USER -rpcpassword=$RPC_PASSWORD -rpcconnect=$RPC_HOST -rpcport=$RPC_PORT getblockchaininfo | jq -r '.verificationprogress')
    if [[ $? -ne 0 ]]; then
        echo "No se pudo obtener el progreso de sincronización. Asegúrate de que bitcoind esté corriendo."
        return 1
    fi

    # Verificar si progress es válido
    if [[ -z "$progress" ]]; then
        echo "No se pudo obtener el progreso de sincronización. Verifica que bitcoind esté corriendo y accesible."
        return 1
    fi

    local progress_percent
    progress_percent=$(echo "$progress * 100" | bc -l)
    echo "Progreso de sincronización: $progress_percent%"
    return 0
}

# Bucle para comprobar el progreso cada 10 segundos
while : ; do
    check_sync_status
    if [[ $? -ne 0 ]]; then
        echo "Error comprobando el estado de sincronización. Revisando logs de bitcoind."
        tail -n 20 ~/.bitcoin/debug.log
        break
    fi
    sleep 10
done


echo "Instalación y configuración de Bitcoin Core completa. Bitcoin Core se ha sincronizado en modo de recorte."
