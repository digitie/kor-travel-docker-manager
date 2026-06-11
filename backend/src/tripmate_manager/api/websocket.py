import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from tripmate_manager.services.docker_service import MANAGED_CONTAINERS, docker_service

logger = logging.getLogger(__name__)
router = APIRouter()

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"New client connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"Client disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.debug(f"Failed to send message to client, disconnecting: {e}")
                self.disconnect(connection)

status_manager = ConnectionManager()

async def status_broadcast_loop():
    """Background task to broadcast container status and metrics every 2 seconds."""
    while True:
        try:
            status = docker_service.get_containers_status()
            await status_manager.broadcast({
                "type": "status",
                "containers": status
            })
        except Exception as e:
            logger.error(f"Error in status broadcast loop: {e}")
        await asyncio.sleep(2)

@router.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    await status_manager.connect(websocket)
    try:
        # Send initial status immediately upon connection
        status = docker_service.get_containers_status()
        await websocket.send_json({
            "type": "status",
            "containers": status
        })
    except Exception as e:
        logger.error(f"Failed to send initial status: {e}")
        status_manager.disconnect(websocket)
        return
        
    try:
        # Keep connection open until client disconnects
        while True:
            # Client can send ping messages or we just wait for disconnect
            await websocket.receive_text()
    except WebSocketDisconnect:
        status_manager.disconnect(websocket)
    except Exception as e:
        logger.debug(f"WS status exception: {e}")
        status_manager.disconnect(websocket)

@router.websocket("/ws/logs/{container_id}")
async def ws_logs(websocket: WebSocket, container_id: str):
    if container_id not in MANAGED_CONTAINERS:
        await websocket.close(code=4000, reason="Invalid container ID")
        return

    await websocket.accept()
    cname = MANAGED_CONTAINERS[container_id]["name"]
    
    try:
        client = docker_service._get_client()
        container = client.containers.get(cname)
    except Exception as e:
        await websocket.send_json({"error": f"Failed to access container: {str(e)}"})
        await websocket.close()
        return

    def get_log_stream():
        # Stream logs, starting with the last 100 lines
        return container.logs(stdout=True, stderr=True, stream=True, follow=True, tail=100)

    try:
        log_stream = await asyncio.to_thread(get_log_stream)
        
        def read_next_line(stream):
            try:
                return next(stream)
            except StopIteration:
                return None
            except Exception:
                return None

        while True:
            # Read next line in a separate thread to prevent blocking the event loop
            line = await asyncio.to_thread(read_next_line, log_stream)
            if line is None:
                await asyncio.sleep(0.5)
                continue
                
            decoded_line = line.decode("utf-8", errors="ignore")
            await websocket.send_json({"log": decoded_line})
            await asyncio.sleep(0.01)
            
    except WebSocketDisconnect:
        logger.info(f"Log streaming websocket disconnected for {container_id}")
    except Exception as e:
        logger.error(f"Error streaming logs for {container_id}: {e}")
    finally:
        try:
            log_stream.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
