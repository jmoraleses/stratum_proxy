#!/bin/bash

# Variables
REPO_URL="https://github.com/benjamin-wilson/public-pool.git"
UI_REPO_URL="https://github.com/benjamin-wilson/public-pool-ui.git"
PROJECT_DIR="public-pool"
UI_PROJECT_DIR="public-pool-ui"
APP_NAME="public-pool"
UI_NAME="ui"
UI_PORT=3336
API_PORT=3334
STRATUM_PORT=3333
PROXY_PORT=3335

# Función para detener la aplicación si está ejecutándose y eliminarla de pm2
stop_and_delete_app() {
  echo "Deteniendo y eliminando la aplicación $1 si está ejecutándose..."
  pm2 stop $1
  pm2 delete $1
}

# Función para liberar un puerto
free_port() {
  echo "Liberando el puerto $1..."
  PID=$(sudo lsof -t -i :$1)
  if [ ! -z "$PID" ]; then
    sudo kill -9 $PID
    echo "Puerto $1 liberado."
  else
    echo "El puerto $1 está libre."
  fi
}

# Actualizar los paquetes del sistema
echo "Actualizando paquetes del sistema..."
sudo apt update && sudo apt upgrade -y

# Instalar dependencias necesarias para Node.js, npm y pm2
echo "Instalando Node.js, npm y pm2..."
sudo apt install -y nodejs npm
sudo npm install -g n pm2
sudo n stable

# Detener y eliminar aplicaciones existentes
stop_and_delete_app $APP_NAME
stop_and_delete_app $UI_NAME

# Liberar los puertos necesarios
free_port $STRATUM_PORT
free_port $API_PORT
free_port $PROXY_PORT
free_port $UI_PORT

# Configurar bitcoin.conf
BITCOIN_CONF_PATH=~/.bitcoin/bitcoin.conf
mkdir -p ~/.bitcoin
echo "Configurando bitcoin.conf..."
cat <<EOT > $BITCOIN_CONF_PATH
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
EOT

# Reiniciar bitcoind
echo "Reiniciando bitcoind..."
bitcoind -daemon

# Clonar el repositorio del backend
if [ ! -d "$PROJECT_DIR" ]; then
  echo "Clonando el repositorio del backend..."
  git clone $REPO_URL
else
  echo "El repositorio del backend ya existe."
fi
cd $PROJECT_DIR

# Verificar y crear el archivo .env si no existe
if [ ! -f ".env" ]; then
  echo "Creando archivo .env con los valores proporcionados..."
  cat <<EOT >> .env
# bitcoin node running in your private network 192.168.1.0/24
BITCOIN_RPC_URL=http://127.0.0.1

# bitcoin node running undockered on the same PC
# needs to add rpcallowip=172.16.0.0/12 to your bitcoin.conf
#BITCOIN_RPC_URL=http://host.docker.internal

BITCOIN_RPC_USER=userbit
BITCOIN_RPC_PASSWORD=passbit
BITCOIN_RPC_PORT=8332
BITCOIN_RPC_TIMEOUT=10000

# Enable in bitcoin.conf with
# zmqpubrawblock=tcp://*:3000
BITCOIN_ZMQ_HOST="tcp://127.0.0.1:3000"

API_PORT=$API_PORT
STRATUM_PORT=$STRATUM_PORT

# optional telegram bot
# TELEGRAM_BOT_TOKEN=

# optional discord bot
# DISCORD_BOT_CLIENTID=
# DISCORD_BOT_GUILD_ID=
# DISCORD_BOT_CHANNEL_ID=

# optional
DEV_FEE_ADDRESS=
# mainnet | testnet
NETWORK=mainnet

API_SECURE=false

ENABLE_SOLO=false
ENABLE_PROXY=true

BRAIINS_ACCESS_TOKEN=

PROXY_PORT=$PROXY_PORT
EOT
  echo "Archivo .env creado."
fi

# Actualizar npm y las dependencias del backend
echo "Actualizando npm..."
sudo npm install -g npm

echo "Instalando dependencias del backend..."
npm install

# Ejecutar el comando npm fund
echo "Ejecutando npm fund..."
npm fund

# Construir el backend para producción
echo "Construyendo el backend para producción..."
npm run build

# Clonar el repositorio del frontend
cd ..
if [ ! -d "$UI_PROJECT_DIR" ]; then
  echo "Clonando el repositorio del frontend..."
  git clone $UI_REPO_URL
else
  echo "El repositorio del frontend ya existe."
fi

# Configurar el archivo .env para el frontend
echo "Creando archivo .env para el frontend..."
cat <<EOT > $UI_PROJECT_DIR/.env
REACT_APP_API_URL=http://localhost:$API_PORT
EOT

# Construir el frontend para producción
cd $UI_PROJECT_DIR
echo "Instalando dependencias del frontend..."
npm install

echo "Ejecutando npm fund para el frontend..."
npm fund

echo "Construyendo el frontend para producción..."
npm run build || { echo "Error: La construcción del frontend falló"; exit 1; }

echo "Todas las instalaciones y configuraciones están completas."

# Iniciar el backend con pm2
cd ../$PROJECT_DIR
echo "Iniciando el backend con pm2..."
pm2 start npm --name $APP_NAME -- run start:dev -f

# Iniciar el frontend con pm2 en el puerto $UI_PORT
cd ../$UI_PROJECT_DIR
echo "Iniciando el frontend con pm2 en el puerto $UI_PORT..."
pm2 serve --spa dist/public-pool-ui/ $UI_PORT --name $UI_NAME -f

echo "Proceso completado. Los proyectos están ejecutándose."

# Verificar el puerto de ejecución del backend
cd ../$PROJECT_DIR
echo "Verificando el puerto en el que se está ejecutando el backend..."
BACKEND_PORT=$(grep -oP 'PROXY_PORT=\K\d+' .env || echo 3000)
echo "El backend se está ejecutando en el puerto $BACKEND_PORT."

# Verificar el puerto de ejecución del frontend
echo "El frontend se está ejecutando en el puerto $UI_PORT."
