.PHONY: help test check doctor clean

help:
	@echo "make test   单元测试 + CLI 冒烟（离线）"
	@echo "make check  单测 + doctor 自检（消耗 1 次额度）"
	@echo "make doctor 只跑自检"
	@echo "make clean  清理运行产物"

test:
	python3 -m unittest discover -s tests -v
	python3 vlearn.py --version
	python3 vlearn.py --help > /dev/null

check:
	python3 -m unittest discover -s tests
	python3 vlearn.py doctor

doctor:
	python3 vlearn.py doctor

clean:
	rm -rf out .tmp .hf __pycache__ tests/__pycache__
