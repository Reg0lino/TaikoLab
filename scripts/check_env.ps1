$ErrorActionPreference = "Stop"
& "$PSScriptRoot\..\.venv\Scripts\python.exe" -c @"
import sys, torch, taikolab
print('Python:', sys.version.split()[0])
print('TaikoLab path:', taikolab.__file__)
print('PyTorch:', torch.__version__)
print('CUDA available:', torch.cuda.is_available())
if not torch.cuda.is_available():
    raise RuntimeError('CUDA is NOT available! Check your NVIDIA driver.')
print('GPU:', torch.cuda.get_device_name(0))
x = (torch.ones(4, device='cuda') * 3).sum().item()
assert x == 12.0, f'Unexpected CUDA math result: {x}'
print('CUDA kernel test: PASS (result =', x, ')')
"@