"""python run.py；默认只监听本机8002。"""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description="EduForge 学情诊断")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--data-dir", help="独立的SQLite报告目录")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["DIAGNOSTICS_DATA_DIR"] = args.data_dir
    import uvicorn
    uvicorn.run("app.main:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
