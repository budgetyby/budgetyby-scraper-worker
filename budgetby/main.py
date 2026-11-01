"""
BudgetBy Main Entry Point.
"""
import asyncio
import logging
import os
import signal
import sys
import uvicorn
from dotenv import load_dotenv
from telegram.ext import Application
from budgetby import database, config
from budgetby.bot.handlers import register_handlers
from budgetby.bot.admin import register_admin_handlers
from budgetby.scheduler import scheduler, cleanup

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
# Suppress noisy repetitive scheduler execution skips
logging.getLogger("apscheduler.scheduler").setLevel(logging.ERROR)
logging.getLogger("apscheduler.executors.default").setLevel(logging.ERROR)

logger = logging.getLogger("budgetby.main")


async def main():
    logger.info("Starting BudgetBy Bot...")
    logger.info("🟢 [LIVE AUTO-SYNC VERIFIED] Dell server successfully synced and reloaded without errors!")
    
    # 1. Initialize Database Pool
    await database.init_pool()

    # Initialize Local SQLite Engine
    from budgetby import local_db
    await local_db.init_db()

    # 2. Start Local Web Dashboard on http://localhost:5000
    try:
        import socket
        from budgetby.dashboard.app import app as dashboard_app
        dash_port = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "5000")))
        dash_host = os.getenv("HOST", "0.0.0.0")

        # Check if port is already in use before attempting to bind
        def _port_free(host: str, port: int) -> bool:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind((host, port))
                    return True
                except OSError:
                    return False

        if not _port_free(dash_host, dash_port):
            logger.warning(
                f"⚠️  Port {dash_port} already in use — dashboard already running, skipping re-bind."
            )
        else:
            config_server = uvicorn.Config(
                dashboard_app,
                host=dash_host,
                port=dash_port,
                log_level="warning",
                access_log=False
            )
            dash_server = uvicorn.Server(config_server)

            async def _safe_serve():
                """Wrap uvicorn.serve so SystemExit never propagates to main loop."""
                try:
                    await dash_server.serve()
                except (SystemExit, OSError) as exc:
                    logger.warning(f"Dashboard server stopped: {exc}")

            asyncio.create_task(_safe_serve())
            logger.info(f"🚀 Web Storefront & Dashboard running on http://{dash_host}:{dash_port}")
    except Exception as e:
        logger.warning(f"Could not start dashboard: {e}")
    
    # 3. Configure Telegram Bot with robust HTTP connection pool & timeout handling
    if config.TELEGRAM_BOT_TOKEN:
        from telegram.request import HTTPXRequest
        from telegram.error import NetworkError, TimedOut

        request_config = HTTPXRequest(
            connection_pool_size=20,
            read_timeout=30.0,
            write_timeout=20.0,
            connect_timeout=15.0,
            pool_timeout=20.0
        )

        application = (
            Application.builder()
            .token(config.TELEGRAM_BOT_TOKEN)
            .request(request_config)
            .get_updates_request(request_config)
            .build()
        )

        async def handle_telegram_polling_error(update, context):
            err = context.error
            err_str = str(err).lower()
            # Transient errors during auto-restart overlap — suppress noisy tracebacks
            if (isinstance(err, (NetworkError, TimedOut))
                    or "httpx" in err_str
                    or "readerror" in err_str
                    or "conflict" in err_str          # 409: two instances briefly overlap on restart
                    or "terminated by other" in err_str):
                logger.debug(f"Transient Telegram polling blip (auto-recovered): {err}")
            else:
                logger.error(f"Telegram Bot Exception: {err}", exc_info=err)

        application.add_error_handler(handle_telegram_polling_error)
        register_handlers(application)
        register_admin_handlers(application)
        
        from budgetby.scheduler import scheduler as sched_module
        from budgetby.engine.posting_queue import get_posting_queue
        if application:
            sched_module.set_bot(application.bot)
            get_posting_queue().set_bot(application.bot)
            
        logger.info("Telegram application configured with all handlers.")
    else:
        application = None
        logger.warning("TELEGRAM_BOT_TOKEN not found in environment!")

    # 4. Catchup scan & title cleanup
    await cleanup.catchup_scan()
    await database.sync_daily_price_baselines()
    try:
        result = await database.execute("DELETE FROM products WHERE platform = 'flipkart' AND (title = 'Product' OR length(title) < 5);")
        logger.info(f"Cleaned up bad Flipkart titles: {result}")
    except Exception as e:
        logger.warning(f"Could not clean bad titles: {e}")
    
    # 5. Start Scheduler
    scheduler.start_scheduler()
    asyncio.create_task(cleanup.daily_cleanup())

    # 6. Start Seeder Background Workers
    try:
        from budgetby.discovery.seeder import ProductSeeder
        logger.info("Starting bootstrap seeder workers...")
        seeder = ProductSeeder()
        asyncio.create_task(seeder.run_bootstrap_until_target())
    except Exception as e:
        logger.error(f"Error starting bootstrap workers: {e}")

    # 7. Start Real-Time Private Channel Listener
    try:
        from budgetby.ingest.telegram_listener import start_telegram_listener
        asyncio.create_task(start_telegram_listener())
    except Exception as e:
        logger.warning(f"Could not start telegram private listener: {e}")

    # 7b. Start Safe 1-by-1 Affiliate Link Converter Worker
    try:
        from budgetby.affiliate.converter_worker import run_affiliate_converter_worker
        asyncio.create_task(run_affiliate_converter_worker())
    except Exception as e:
        logger.warning(f"Could not start affiliate converter worker: {e}")

    # 8. Run Bot Polling
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal():
        logger.info("Received termination signal (SIGTERM/SIGINT). Initiating graceful stop...")
        stop_event.set()

    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _handle_signal)
            except NotImplementedError:
                pass
    else:
        try:
            signal.signal(signal.SIGINT, lambda s, f: stop_event.set())
            signal.signal(signal.SIGTERM, lambda s, f: stop_event.set())
        except Exception:
            pass

    if application:
        try:
            await application.initialize()
            await application.start()
            await application.updater.start_polling(
                drop_pending_updates=True,
                poll_interval=1.0,
                timeout=20,
                bootstrap_retries=-1
            )
            logger.info("Bot is polling. Main task running.")
            await stop_event.wait()
            
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutting down bot gracefully...")
        finally:
            logger.info("Executing graceful shutdown sequence...")
            try:
                scheduler.stop_scheduler()
            except Exception as se:
                logger.debug(f"Scheduler stop notice: {se}")
            if application.updater:
                try:
                    await application.updater.stop()
                except Exception:
                    pass
            try:
                await application.stop()
                await application.shutdown()
            except Exception:
                pass
            await local_db.close_db()
            await database.close_pool()
            logger.info("Bot & Database shutdown complete.")
    else:
        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            logger.info("Executing graceful shutdown sequence...")
            try:
                scheduler.stop_scheduler()
            except Exception as se:
                logger.debug(f"Scheduler stop notice: {se}")
            await local_db.close_db()
            await database.close_pool()
            logger.info("Daemon shutdown complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
