"""本地启动：python run.py [--port 8001] [--load-samples]"""
import argparse
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="EduForge 教育知识库")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--data-dir", default=None, help="数据目录（默认 runtime/）")
    parser.add_argument("--load-samples", action="store_true", help="启动前导入内置合成演示资料")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("缺少依赖：pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)

    from app.main import create_app

    app = create_app(args.data_dir)
    if args.load_samples:
        res = app.state.service.load_samples()
        print(f"演示资料：新导入 {len(res['imported'])} 份，跳过 {len(res['skipped'])} 份")
    print(f"打开 http://{args.host}:{args.port}  （API 文档 /api/docs）")
    if args.reload:
        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=True)
    else:
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
