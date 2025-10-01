import math
import os
import aiofiles
import uvicorn
from fastapi import FastAPI, UploadFile,Request
from pydantic import BaseModel

app = FastAPI()
uploaded_files = []
MAX_UPLOAD=21
UPLOAD_DIR="uploads"
os.makedirs(UPLOAD_DIR,exist_ok=True)

@app.post("/images")
#此接口用于获取用户上传的图片数量n
async def upload(files: list[UploadFile]):
    if len(uploaded_files)+len(files)>MAX_UPLOAD:
        raise HTTPException(status_code=400,detail=f"最多只能上传{MAX_UPLOAD}张图片")
    for f in files:
        file_path=os.path.join(UPLOAD_DIR,f.filename)
        async with aiofiles.open(file_path,"wb")as buffer:
            await buffer.write(await f.read())
        #这里给每个上传的图片加上文件名，文件路径，是否已被分组以及在组别中的排名信息
        uploaded_files.append({"filename":f.filename,"filepath":file_path,"sorted":False,"rank":None})
    n = len(uploaded_files)
    return {"num_uploaded": n,"max_upload":MAX_UPLOAD}

def format_groups(uploaded_files,key):
    #这是一个辅助函数，用来根据提供的key生成groups
    groups={}
    for img_dict in uploaded_files:
        value=img_dict.get(key)
        if value is not None:
            groups.setdefault(f"group_{value}",[]).append({"filename":img_dict["filename"],"filepath":img_dict["filepath"]})
    return groups

def find_best_matrix(n):
    #对n进行开根，找到最接近n的开根数的两个因数(即矩阵的长和宽)，得到a*b=n或a*b=n+i(i即补全空位数)
    root=math.isqrt(n)

    #情况1：n是完全平方数
    if root*root==n:
        return (root,root,0)

    #情况2：n不是完全平方数，但可被两个不等于1或n的整数分解
    for cols in range(root,1,-1):
        if n%cols==0:
            rows=n//cols
            return (rows,cols,0)

    #情况3:n是质数，既不符合情况1，也不符合情况2，那么将情况3引导向情况1或情况2
    i1=root*(n//root+1)-n
    i2=(root+1)*(n//(root+1)+1)-n
    if i1<=i2:
        long_side=max(root,n//root+1)
        short_side=min(root, n // root + 1)
        return (long_side,short_side,i1)
    else:
        long_side = max(root+1, n // (root+1) + 1)
        short_side = min(root+1, n // (root+1) + 1)
        return (long_side,short_side,i2)

@app.get("/images/get")
def get_groups():
    #将矩阵中的图片按矩阵长度进行分组，但只需要设置group的属性，把每一个group当成一个对象来处理
    rows, cols, blank_space = find_best_matrix(len(uploaded_files))
    file_index = 0
    for i in range(rows):
        group_name=f"group_{i+1}"
        for j in range(cols):
            if file_index<len(uploaded_files):
                img_dict=uploaded_files[file_index]  # 这里取出列表uploaded_files中当前下标对应的字典，赋值给了img_dict，而img_dict就成为了当前正在处理图片的地址
                img_dict["group"]=group_name
                file_index+=1
    return format_groups(uploaded_files,"group")

class SortRequest(BaseModel):
    group:str
    order:list[str]
@app.post("/images/sort")
def sort_groups(req:SortRequest):
    for idx,filename in enumerate(req.order,start=1):
        for img in uploaded_files:
            if img["filename"]==filename:
                img["rank"]=idx
    return format_groups(uploaded_files,"rank")



def filled_by_pic(uploaded_files):
    #将用户上传的图片填入空白矩阵
    rows,cols,blank_space=find_best_matrix(len(uploaded_files))
    matrix = []
    file_index=0
    for i in range(rows):
        row=[]
        for j in range(cols):
            if file_index<len(uploaded_files):
                row.append(uploaded_files[file_index])
                file_index+=1
            else:
                row.append(None)
        matrix.append(row)
    return matrix


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)