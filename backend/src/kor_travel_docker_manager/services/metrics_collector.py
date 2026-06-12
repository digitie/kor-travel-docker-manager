import asyncio
import datetime
import logging

from kor_travel_docker_manager.services.docker_service import MANAGED_CONTAINERS, docker_service
from kor_travel_docker_manager.services.metrics_service import metrics_service

logger = logging.getLogger(__name__)

class MetricsCollector:
    def __init__(self):
        self._task = None
        self._running = False
        # 이전 IO 누적값 보관용 (container_id -> (prev_read_bytes, prev_write_bytes))
        self._prev_io = {}
        # 최신 메트릭 메모리 캐시 (container_id -> metric_dict)
        self._latest_metrics = {}

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._collect_loop())
            logger.info("Metrics collector background task started.")

    def stop(self):
        if self._running:
            self._running = False
            if self._task:
                self._task.cancel()
            logger.info("Metrics collector background task stopped.")

    def get_latest_metric(self, container_id: str) -> dict:
        """Get the latest cached metric or default empty values."""
        return self._latest_metrics.get(container_id, {
            "timestamp": "",
            "cpu_pct": 0.0,
            "mem_pct": 0.0,
            "mem_usage": 0,
            "mem_limit": 0,
            "io_read": 0,
            "io_write": 0
        })

    async def _collect_loop(self):
        cleanup_counter = 0
        # 최초 기동 시 데이터베이스 정리 한 번 실행
        try:
            metrics_service.cleanup_old_metrics()
        except Exception as e:
            logger.error(f"Initial old metrics cleanup failed: {e}")

        while self._running:
            try:
                # 30일 경과 데이터 청소 (대략 1시간에 한번씩 실행 - 10초 * 360 = 3600초 = 1시간)
                cleanup_counter += 1
                if cleanup_counter >= 360:
                    metrics_service.cleanup_old_metrics()
                    cleanup_counter = 0

                # 컨테이너 메트릭 수집
                await self.collect_metrics()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in metrics collection loop: {e}")
            
            # 10초 대기
            await asyncio.sleep(10)

    async def collect_metrics(self):
        try:
            client = docker_service._get_client()
        except Exception:
            # Docker 데몬이 구동 중이지 않거나 접근 불가능할 때
            return

        for key, spec in MANAGED_CONTAINERS.items():
            cname = spec["name"]
            try:
                container = client.containers.get(cname)
                if container.status != "running":
                    # 컨테이너가 실행 중이지 않으면 메트릭 리셋
                    self._latest_metrics[key] = {
                        "timestamp": "",
                        "cpu_pct": 0.0,
                        "mem_pct": 0.0,
                        "mem_usage": 0,
                        "mem_limit": 0,
                        "io_read": 0,
                        "io_write": 0
                    }
                    continue
                
                # stats API 호출 (I/O 차단 방지를 위해 스레드 풀에서 비동기로 실행)
                stats = await asyncio.to_thread(container.stats, stream=False)
                
                # 1. CPU 사용률 계산
                cpu_pct = 0.0
                cpu_stats = stats.get("cpu_stats", {})
                precpu_stats = stats.get("precpu_stats", {})
                
                cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
                system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)
                
                if system_delta > 0 and cpu_delta > 0:
                    online_cpus = cpu_stats.get("online_cpus")
                    if not online_cpus:
                        online_cpus = len(cpu_stats.get("cpu_usage", {}).get("percpu_usage", [1]))
                    cpu_pct = (cpu_delta / system_delta) * online_cpus * 100.0

                # 2. 메모리 점유율 계산
                mem_usage = stats.get("memory_stats", {}).get("usage", 0)
                mem_limit = stats.get("memory_stats", {}).get("limit", 1)
                mem_pct = (mem_usage / mem_limit) * 100.0 if mem_limit > 0 else 0.0

                # 3. 디스크 IO 계산 (10초 델타)
                blkio_stats = stats.get("blkio_stats", {})
                curr_read = 0
                curr_write = 0
                
                for io_entry in blkio_stats.get("io_service_bytes_recursive", []):
                    op = io_entry.get("op", "").lower()
                    val = io_entry.get("value", 0)
                    if "read" in op:
                        curr_read += val
                    elif "write" in op:
                        curr_write += val

                prev_read, prev_write = self._prev_io.get(key, (None, None))
                
                if prev_read is None or curr_read < prev_read:
                    delta_read = 0
                else:
                    delta_read = curr_read - prev_read
                
                if prev_write is None or curr_write < prev_write:
                    delta_write = 0
                else:
                    delta_write = curr_write - prev_write
                
                self._prev_io[key] = (curr_read, curr_write)

                # SQLite에 메트릭 기록 저장
                metrics_service.save_metric(
                    container_id=key,
                    cpu_pct=cpu_pct,
                    mem_usage=mem_usage,
                    mem_limit=mem_limit,
                    mem_pct=mem_pct,
                    io_read=delta_read,
                    io_write=delta_write
                )

                # 최신 메트릭을 캐시에 저장
                self._latest_metrics[key] = {
                    "timestamp": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                    "cpu_pct": round(cpu_pct, 1),
                    "mem_pct": round(mem_pct, 1),
                    "mem_usage": mem_usage,
                    "mem_limit": mem_limit,
                    "io_read": delta_read,
                    "io_write": delta_write
                }
                
            except Exception as e:
                # 컨테이너 상태 변경 중이거나 접근이 불가한 경우 디버그 로그 기록
                logger.debug(f"Metrics collection failed for container {cname}: {e}")

metrics_collector = MetricsCollector()
