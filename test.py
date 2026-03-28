import math
import os
import aiofiles
import uvicorn
import uuid
from fastapi import FastAPI, UploadFile,HTTPException,Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

class Variable:
    def __init__(self):
        #文件处理
        self.uploaded_files = []
        self.file_map = {}  #用于存储文件名，以对文件属性进行映射，其格式为{"文件名":图片对象}
        #全局变量缓存
        self.last_request = None  #延迟处理（即缓存）用户提交的排序请求
        self.relations=set() #记录用户处理过的所有图片形成的顺序集
        self.groups_to_return=[] #用于存储需要返回给前端的多个组别dict，每个dict内包含了同组内多个图片各自的信息
        self.matrices={} #矩阵中一行内图片数量若大于3，即可视为一个小矩阵
        self.rows=[] #矩阵中一行内图片数量若小于4，即可视为一个可直接输出的图片列表
        self.matrix_queue=[]  #未经处理的matrix列表
        self.current_matrix=None #当前正在处理的matrix指针
        self.matrix = None
        self.compare_group={}  #compare_group会是一个包含一个包含两张或三张图片的名字的列表的字典
        self.first_compare=None #初始化compare_images中首次图片比较状况，若执行过set_start则设置为True
        self.processed_images=[]
        self.final_matrix = []  #最终的matrix形态，其结构与先前的各种小型matrix的结构相同，只负责存储图片名并进行映射
        self.stage=None #用于反映程序状态

app = FastAPI()
app.add_middleware(
    CORSMiddleware, #type:ignore
    allow_origins=["*"],  # 允许所有来源（仅用于测试）
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/uploads",StaticFiles(directory="uploads"),name="uploads")  #将文件夹"uploads"中的静态文件映射到网站路径"/uploads"中
MAX_UPLOAD=27
MIN_UPLOAD=6
UPLOAD_DIR="uploads"
os.makedirs(UPLOAD_DIR,exist_ok=True)
tasks={} #结构为"{"task_id_1":Variable(),
#                "task_id_2":Variable(),
#                ...}"

def create_task_logic():
    task_id = str(uuid.uuid4()) #随机生成不可能重复的字符串id
    tasks[task_id] = Variable() #每一个task_id都代表着一次完整的排序，而每一个Variable都有独属于自己的"uploaded_files"、"matrix"、"compare_group"，等等，且完全互不影响
    return task_id
@app.post("/task/create")
def create_task_api():
    task_id = create_task_logic() #为每个用户创建了一个独一无二的id，以进行临时会话
    return {"task_id": task_id}

def get_task(task_id: str) -> Variable: #承诺这个函数在设计上会返回一个Variable对象
    if task_id not in tasks: #用于防止前端乱传或task过期/被清理掉的情况，属于防御式编程
        raise HTTPException(status_code=400, detail="Invalid task_id")
    return tasks[task_id]

@app.get("/task/{task_id}/status")
# 此接口用于获取任务的当前状态，以在前端刷新后恢复（可应用于突发情况，不属于正常流程的一部分
def get_task_status(task_id: str): #防止诸如浏览器刷新、屏幕锁屏、网络中断...的情况
    gv = get_task(task_id)
    status_info = {
        "uploaded_count": len(gv.uploaded_files),
        "processed_images_count": len(gv.processed_images),
        "has_compare_group": bool(gv.compare_group), #判断是否有需要比较的图片以返回给前端让用户继续进行比较
        "has_matrix": gv.matrix is not None #判断“矩阵”是否已然出现，以判断系统现在正进入哪个排序阶段（质问“矩阵”是否正在驱动排序
    } #可将其理解为每个task的内部状态快照
    if gv.compare_group and "group" in gv.compare_group: # 如果有当前需要比较的图片，就返回给前端
        status_info["current_images"] = gv.compare_group["group"]
    return status_info

#流程的开始
@app.post("/images")
#此接口用于获取用户上传的图片数量n
async def upload(request:Request,files: list[UploadFile]):
    task_id = request.headers.get("X-Task-ID")
    gv = get_task(task_id)
    if len(gv.uploaded_files)+len(files)>MAX_UPLOAD:
        raise HTTPException(status_code=400,detail=f"最多只能上传{MAX_UPLOAD}张图片")
    if len(gv.uploaded_files)+len(files)<MIN_UPLOAD:
        raise HTTPException(status_code=400,detail=f"至少上传{MIN_UPLOAD}张图片")
    for f in files:
        file_path=os.path.join(UPLOAD_DIR,f.filename)
        async with aiofiles.open(file_path,"wb")as buffer:
            await buffer.write(await f.read())
        #这里给每个上传的图片加上文件名，文件路径，是否已被分组以及在组别中的排名信息，另外"superior"属性代表着这张图片所优于的图片合集
        img_obj={"filename":f.filename,"filepath":file_path,"rank":None,"superior":[],"row":None,"col":None,"group_id":None}
        gv.uploaded_files.append(img_obj)
        gv.file_map[f.filename]=img_obj
    return {"num_uploaded": len(gv.uploaded_files),"max_upload":MAX_UPLOAD}

def build_direct_relations(gv: Variable, order: list[str]) -> list[tuple]:
    """构建直接关系并进行循环检测"""
    file_map = gv.file_map
    direct_relations = []
    for i in range(len(order)):
        superior_img = file_map.get(order[i])
        if not superior_img:
            continue
        for j in range(i + 1, len(order)):
            inferior_img = file_map.get(order[j])
            if not inferior_img:
                continue
            # 循环检测
            if superior_img["filename"] == inferior_img["filename"]:
                raise ValueError(f"自我闭环: {superior_img['filename']}")
            if superior_img["filename"] in inferior_img["superior"]:
                raise ValueError(
                    f"外部闭环: {superior_img['filename']} 已在 {inferior_img['filename']} 的 'superior' 中"
                )
            direct_relations.append((superior_img, inferior_img))
            gv.relations.add((superior_img["filename"], inferior_img["filename"]))
    return direct_relations

def propagate_relations(gv: Variable, direct_relations: list[tuple]):
    """传播间接关系"""
    if not direct_relations:
        return
    file_map = gv.file_map
    changed = True
    iteration = 0
    max_iter = 100
    while changed and iteration < max_iter:
        changed = False
        iteration += 1
        for superior_name, inferior_name in list(gv.relations):
            superior_img = file_map.get(superior_name)
            inferior_img = file_map.get(inferior_name)
            if not superior_img or not inferior_img:
                continue
            old_len = len(superior_img["superior"])
            new_set = set(superior_img["superior"])
            new_set.add(inferior_name)
            new_set.update(inferior_img["superior"])
            new_set.discard(superior_name)
            if len(new_set) > old_len:
                superior_img["superior"] = list(new_set)
                changed = True
    if iteration == max_iter:
        print('WARNING: 传播迭代次数到达上限')

def update_superior(gv: Variable, order: list[str]):
    """主函数：更新图片的上下级关系"""
    if len(order) < 2:
        return
    # 构建直接关系并检测循环
    direct_relations = build_direct_relations(gv, order)
    # 批量更新直接关系
    for superior_img, inferior_img in direct_relations:
        new_set = set(superior_img["superior"])
        new_set.add(inferior_img["filename"])
        new_set.update(inferior_img["superior"])
        new_set.discard(superior_img["filename"])
        superior_img["superior"] = list(new_set)
    # 传播间接关系
    propagate_relations(gv, direct_relations)

def format_groups(upload_file,key:str):
    #这是一个辅助函数，用来根据提供的key（这里主要会是图片的"rank"属性）生成groups
    groups={}
    for img_dict in upload_file:
        value=img_dict.get(key)
        if value is not None:
            groups.setdefault(f"{value}",[]).append({
                "filename": img_dict.get("filename"),
                "filepath": img_dict.get("filepath"),
                "rank": img_dict.get("rank"),
                "superior": img_dict.get("superior", []),
                "group": img_dict.get("group"),
                "group_id":img_dict.get("group_id")
            })
    return groups

class SortRequest(BaseModel):
    group:str
    order:list[str]   #即用户的排序结果
def apply_user_order(gv,order:list[str]):

    print(f"\n{'=' * 50}")
    print(f"apply_user_order 被调用")
    print(f"order = {order}")

    # 记录调用前的 rank
    print("\n调用前的 rank:")
    rank_before = {}
    for filename in order:
        img = gv.file_map.get(filename)
        if img:
            rank_before[filename] = img.get('rank')
            print(f"  {filename}: rank={img.get('rank')}")

    for idx,filename in enumerate(order,start=1): # 这里会拿取用户排序后的结果并依次对图片的"rank"进行赋值
        for img in gv.uploaded_files:
            if img["filename"]==filename:
                img["rank"]=idx
    update_superior(gv,order)

    # 记录调用后的 rank
    print("\n调用后的 rank:")
    for filename in order:
        img = gv.file_map.get(filename)
        if img:
            print(f"  {filename}: rank={img.get('rank')}")

    # 检查 rank 是否被改变
    print("\nrank 变化:")
    for filename in order:
        before = rank_before.get(filename)
        after = gv.file_map[filename]['rank']
        if before != after:
            print(f"  {filename}: {before} -> {after}")

    print(f"{'=' * 50}\n")

@app.post("/images/sort")
def assign_rank_by_user(req:SortRequest,request:Request): #apply_user_order的实际应用层
    task_id = request.headers.get("X-Task-ID")
    gv = get_task(task_id)
    apply_user_order(gv,req.order)
    return {"status":"ok"} #确认状态正常

@app.get("/images/get",response_model=None)
def get_groups(request:Request):
    #将矩阵中的图片按矩阵长度进行分组（每一列就是一组），但只需要设置group的属性，把每一个group当成一个对象放在format_groups中来处理
    task_id = request.headers.get("X-Task-ID")
    gv = get_task(task_id)
    n=len(gv.uploaded_files)
    num_groups=(math.ceil(n/3) )
    group_max_size=3
    temp_files=gv.uploaded_files.copy() #对文件列表进行浅拷贝，但里面的字典依然是同个对象
    file_index = 0
    for i in range(num_groups): #矩阵的每一列为一组
        group_name=f"group_{i+1}"
        for j in range(group_max_size):
            if file_index<len(temp_files):
                img_dict=temp_files[file_index]  # 这里取出列表uploaded_files中当前下标对应的字典，赋值给了img_dict，而img_dict就成为了当前正在处理图片的地址
                img_dict["group"]=group_name
                file_index+=1
    groups=format_groups(gv.uploaded_files,"group") #groups的结构类似于{"group_1":[{img1},{img2},...],"group_2":[...],...}（每组内最多存在三张图片
    for group_name,images in groups.items():
        if len(images) == 1:
            images[0]["rank"] = 1
            return gv.groups_to_return
        else:
            gv.groups_to_return.append({"group_id": group_name, "images":images})
    return gv.groups_to_return #返回结果给用户排序

def sort_groups(group) -> dict[str,list[dict]]:
    # 用来根据每组图片的数量来拆分组别，生成新的小组
    if not group or "rank" not in group[0] or group[0]["rank"] is None:  # 防御工程🛡️
        return {} #数据结构出现问题，程序需要终止
    n=len(group)
    a=b=0
    for i in range(n//3,-1,-1):
        if (n-3*i)%2==0:
            a=i
            b=(n-3*i)//2
            break
    groups={}
    rank=group[0]["rank"]  #默认同组内每张图片的"rank"属性相同，因此只用取首张图片的"rank"属性即可
    for f in range(a):
        groups[f"group_{rank}_{f+1}"]=group[f*3:(f+1)*3]
    for j in range(b):
        start=a*3+j*2
        groups[f"group_{rank}_{a+j+1}"]=group[start:start+2]
    return groups #结构大致为{"group_1_1":[],"group_1_2":[],...,"group_1_a":[],"group_2_b":[],...,"group_c_d":[]}

@app.post("/images/split")
def split_groups(request:Request):
    # 将每个大组内的图片按照sort_groups的规则分为多个小组并对每个小组内图片进行排序
    task_id = request.headers.get("X-Task-ID")
    gv = get_task(task_id)
    gv.groups_to_return=[]
    for img in gv.uploaded_files:
        if not img["rank"]:
            img["rank"]=1
    big_groups = format_groups(gv.uploaded_files, "rank")  # big_groups -> dict[str,list[dict]]
    for big_group_name, imgs in big_groups.items():
        for group_id,images in sort_groups(imgs).items():
            for img in images: #给每一组内的每个图片对象设置"group_id"属性
                gv.file_map.get(img["filename"])["group_id"]=group_id
            gv.groups_to_return.append({"group_id":group_id,"images":images})
    return gv.groups_to_return #返回结果给用户排序以得到每个小组内每张图片的"rank"，以在regroup中进行对于gv.matrices中每个matrix内的多个group的重组

def get_structures(gv:Variable):
    #初始化structures，其结构为{"matrix_1":{"group_1":{"group_id":...,"images":[...]},...,"group_i":{...}},...,"matrix_i":{...}}
    structures={}
    for img in gv.uploaded_files:
        matrix_name = f"matrix_{img['group_id'].split('_')[1]}" #大矩阵中每一行小矩阵的名字
        group_name = f"group_{img['group_id'].split('_')[2]}" #小矩阵中每一行组别的名字
        if matrix_name not in structures:
            structures[matrix_name] = {} # 如果小矩阵不存在于大矩阵中，就创建一个dict用于存储小矩阵
        if group_name not in structures[matrix_name]:
            structures[matrix_name][group_name]={"group_id":img["group_id"],"images":[]} # 如果某个组别不存在于小矩阵中，就创建一个dict用于存储对应组别
        structures[matrix_name][group_name]["images"].append({
            "filename": img.get("filename"),
            "filepath": img.get("filepath"),
            "rank": img.get("rank"),
            "superior": img.get("superior", []),
            "group": img.get("group"),
            "group_id": img.get("group_id")
        })
    matrix_names = sorted(structures.keys())
    structures = {name: structures[name] for name in matrix_names}
    return structures

def get_matrices_and_rows(gv:Variable):

    print("\n=== 开始 get_matrices_and_rows ===")

    # 先打印当前所有图片的 rank
    print("当前所有图片的 rank:")
    for img in gv.uploaded_files:
        print(f"  {img['filename']}: rank={img['rank']}, group_id={img.get('group_id')}")

    # 获取 structures
    structures = get_structures(gv)

    print("\n生成的 structures:")
    for matrix_name, matrix_dict in structures.items():
        print(f"  {matrix_name}:")
        for group_name, group_data in matrix_dict.items():
            print(f"    {group_name}:")
            filenames = [img["filename"] for img in group_data["images"]]
            ranks = [img["rank"] for img in group_data["images"]]
            print(f"      图片: {filenames}")
            print(f"      rank: {ranks}")

    #防御式编程，用于防止对全局变量matrices和rows的重复追加
    gv.matrices = {}
    gv.rows = []
    #这个def用于从structures中分离出matrices与rows
    structures=get_structures(gv)
    for matrix_name,matrix_dict in structures.items():
        if len(matrix_dict)==1:
            group=next(iter(matrix_dict.values()))
            filenames=[img["filename"] for img in group["images"]]
            print(f"准备添加行: {filenames}")
            gv.rows.append(filenames) #row存储图片名而非图片对象，下方的matrices同理
            print(f"添加后 gv.rows = {gv.rows}")
            for i,row in enumerate(gv.rows): #根据每行内各个图片的排名进行重组
                gv.rows[i]=sorted(row,key=lambda filename:gv.file_map[filename]["rank"])
        else:
            gv.matrices[matrix_name]=matrix_dict #matrices的结构大致为{"matrix_1":[...],"matrix_2":[...],...}
    print(f"get_matrices_and_rows 结束时 gv.rows = {gv.rows}")

@app.post("/images/regroup")
def regroup_matrices(request:Request):
    #对matrices中每个matrix内的多个group(根据group内各个图片的"rank")进行重组并返回matrices给用户，让用户对matrices中存在的每一个group进行比较
    task_id = request.headers.get("X-Task-ID")
    gv = get_task(task_id)

    print(f"regroup_matrices 开始: matrices={gv.matrices}, rows={gv.rows}") #调试2.1

    if not gv.matrices:
        if not gv.rows:
            get_matrices_and_rows(gv)
            regroup_matrices(request)
        else:
            print("🔍 进入 rows 分支")
            print(f"合并前 rows = {gv.rows}")

            merge_rows_into_final_matrix(gv)
            print(f"合并后 matrix = {gv.matrix}")

            get_coordinate(gv)
            print(f"设置坐标后，检查第一张图片坐标: {gv.matrix[0][0]} 的 row,col = {gv.file_map[gv.matrix[0][0]]['row']},{gv.file_map[gv.matrix[0][0]]['col']}")

            set_start(gv)
            print(f"set_start 后 compare_group = {gv.compare_group}")
            print(f"processed_images = {gv.processed_images}")

            return {"action": "compare", "matrix": gv.matrix}
    for matrix_id, matrix_dict in gv.matrices.items():

        print(f"matrix_id={matrix_id}, type(matrix_dict)={type(matrix_dict)}")
        print(f"matrix_dict keys: {matrix_dict.keys() if isinstance(matrix_dict, dict) else 'not a dict'}")

        group = {}
        for group_id,group_data in matrix_dict.items():

            print(f"  group_id={group_id}, type(group_data)={type(group_data)}")

            images = group_data["images"]
            for image in images:
                group_rank = f"group_{image['rank']}"
                if group_rank not in group:
                    group[group_rank] = []
                group[group_rank].append(image)
        gv.matrices[matrix_id] = group
    return{
        "matrices":[{
            "matrix_id":matrix_id,
            "group":[{
                "group_id":group_id,
                "images":[{
                    "filename":img["filename"],
                    "filepath":img["filepath"]
                }
                    for img in images]
            }
            for group_id,images in matrix.items()]
        }
        for matrix_id,matrix in gv.matrices.items()]
    }

def next_small_matrix(gv: Variable):
    #这是一个辅助函数，用于挑选下一个用于处理的小型矩阵
    if not gv.matrix_queue and gv.current_matrix is None: #初始化待处理的小型矩阵列表
        gv.matrix_queue=list(gv.matrices.keys())
        gv.current_matrix=None
    if not gv.matrix_queue: #说明此时所有matrices中的matrix都已处理完成，可进入final_matrix的处理
        return None
    gv.current_matrix = gv.matrix_queue.pop(0) #从上往下依次处理存储在matrix_queue中的matrix列表（final_matrix中包含超过三张图片的一行视作一个matrix
    return gv.current_matrix

def get_matrix(gv: Variable):
    #初始化偏序矩阵
    gv.matrix = []
    gv.current_matrix=next_small_matrix(gv)
    matrix_name = gv.current_matrix #得到当前需要进行处理的矩阵指针
    if matrix_name is None: #证明所有的矩阵都已得到处理，可以直接跳转到最终矩阵的处理
        gv.matrix=None
        return None
    matrix_dict = gv.matrices[matrix_name] #一个矩阵代表了一个dict
    for group_id,images in matrix_dict.items():
        sorted_group=sorted(images,key=lambda x:x["rank"])#先根据用户排序后的结果对matrices内每个matrix内的group进行重组
        gv.matrix.append([img["filename"] for img in sorted_group])

def get_coordinate(gv:Variable):
    #设置每个图片在矩阵中的坐标
    if not gv.matrix:
        get_matrix(gv)
    for row,filenames in enumerate(gv.matrix): #注意matrix里存储的是图片名而不是图片对象
        for col,filename in enumerate(filenames):
            img_obj=gv.file_map.get(filename)
            if img_obj:
                img_obj["row"] = row
                img_obj["col"] = col

def set_start(gv:Variable):
    #设置图片比较的出发点
    if gv.first_compare: #查询该函数是否已被利用过
        return {"message:":"已进行过首次图片查找，请重新确认状态。"}
    if not gv.matrix:
        get_matrix(gv)
    for row_idx,filenames in enumerate(gv.matrix):
        for col_idx,filename in enumerate(filenames):
            img_obj=gv.file_map.get(filename)
            if img_obj and row_idx==0 and col_idx==0:
                img_obj["rank"]=1  #默认坐标为(0,0)的图片排名第一
                if img_obj["filename"] not in gv.processed_images:
                    gv.processed_images.append(img_obj["filename"])
    right_up_filename=gv.matrix[0][1]
    left_down_filename=gv.matrix[1][0]
    gv.compare_group={"group":[left_down_filename, right_up_filename]} #默认排名第一的图片的右方和下方的图片为第一个需要进行比较的组别
    gv.first_compare=True #执行结束后设置状态以防止二次更新

def matrix_for_compare(gv:Variable):
    # 尝试获取下一个矩阵
    next_small_matrix(gv)
    if gv.current_matrix:
        get_matrix(gv)
        get_coordinate(gv)
        set_start(gv)
        print("切换到下一个矩阵")
        return compare_images_logic(gv)  # 直接返回新矩阵的比较组
    else:
        # 没有下一个矩阵了，进入最终阶段
        gv.compare_group = None
        print("所有矩阵处理完成")
        return None

def left_or_right(gv:Variable):
    #用于判断一组中两张图片各自处于左下或右上的位置并进行标记
    group_filenames = gv.compare_group["group"]
    f1,f2=group_filenames[0],group_filenames[1]
    img1=gv.file_map[f1]
    img2=gv.file_map[f2]
    if img1["row"] > img2["row"] and img1["col"] < img2["col"]:
        left_down = f1
        right_up = f2
    else:
        left_down = f2
        right_up = f1
    return left_down,right_up

def last_left_down(gv:Variable):
    #用于处理left_down出现在最后一行的特殊情况，会依次设置最后一行中各个图片的rank
    last_row=gv.matrix[-1] #最后一行存文件名
    for filename in last_row:
        if filename not in gv.processed_images:
            img_obj=gv.file_map[filename]
            img_obj["rank"]=len(gv.processed_images) + 1
            gv.processed_images.append(filename)

def last_right_up(gv:Variable):
    #用于处理right_up出现在最后一列的特殊情况，会依次设置最后一列中各个图片的rank
    col_idx=len(gv.matrix[0])-1 #最后一列索引
    for row in gv.matrix:
        filename=row[col_idx]
        if len(row)==len(gv.matrix[0]) and filename not in gv.processed_images:
            img_obj=gv.file_map[filename]
            img_obj["rank"]=len(gv.processed_images) + 1
            gv.processed_images.append(filename)

def find_left_down(gv:Variable,left_down_filename,right_up_filename):
    #先设置好有关left_down和right_up的一切
    if not hasattr(gv, 'search_path'):
        gv.search_path = []
    current_pair = (left_down_filename, right_up_filename)
    gv.search_path.append(current_pair)
    print(f"🔍 搜索路径: {' -> '.join([f'{a}>{b}' for a, b in gv.search_path])}")
    left_down=gv.file_map[left_down_filename]
    if left_down_filename not in gv.processed_images:
        left_down["rank"] = len(gv.processed_images) + 1
        gv.processed_images.append(left_down_filename)
    left_down_row,left_down_col=left_down["row"],left_down["col"]
    right_up=gv.file_map[right_up_filename]
    right_up_row, right_up_col = right_up["row"], right_up["col"]
    #然后开始寻找新的左下图
    if right_up_col-left_down_col>1: #说明此时需要转到compare_three_images来处理
        up_filename=right_up_filename
        middle_filename=gv.matrix[left_down_row][left_down_col+1]
        down_filename=gv.matrix[left_down_row+1][left_down_col]
        gv.compare_group = {"group": [up_filename,middle_filename,down_filename]}

        result=compare_three_images(gv)
        print(f"find_left_down 返回(three): {result}")
        return result
    if left_down_row + 1 < len(gv.matrix):
        left_down["row"] += 1 # 左下图片下移一格
        new_left_down_filename = gv.matrix[left_down["row"]][left_down["col"]]  # 更新left_down的代称
        gv.compare_group = {"group": [new_left_down_filename, right_up_filename]}
        result = compare_two_images(gv) # 处理更新后的left_down与right_up
        print(f"find_left_down 返回(two): {result}")
        return result
    else: #说明left_down需要换列，此时right_up成为了未经过比较的图片组成的矩阵的左上角
        if right_up_col==len(gv.matrix[right_up_row])-1: #那么right_up在最后一列，说明此矩阵中的图片排序结束，可以进行对下一个矩阵的处理
            last_right_up(gv)
            gv.compare_group = None

            result = matrix_for_compare(gv)
            print(f"find_left_down 返回(end): {result}")
            print("请再次调用'compare_group'接口")
            print(f"find_left_down 返回 None 前: matrix_queue={gv.matrix_queue}")
            return result
        if right_up_filename not in gv.processed_images:
            right_up["rank"] = len(gv.processed_images) + 1
            gv.processed_images.append(right_up_filename)
        left_down_filename=gv.matrix[right_up_row+1][right_up_col]
        right_up["col"]+=1
        new_right_up_filename=gv.matrix[right_up["row"]][right_up["col"]] #更新right_up的代称
        gv.compare_group = {"group": [left_down_filename, new_right_up_filename]}

        result = compare_two_images(gv)
        print(f"find_left_down 返回(new): {result}")
        return result

def find_right_up(gv:Variable,left_down_filename,right_up_filename):
    if not hasattr(gv, 'search_path'):
        gv.search_path = []
    current_pair = (left_down_filename, right_up_filename)
    gv.search_path.append(current_pair)
    print(f"🔍 搜索路径: {' -> '.join([f'{a}>{b}' for a, b in gv.search_path])}")
    #这里与find_left_down同理
    right_up = gv.file_map[right_up_filename]
    if right_up_filename not in gv.processed_images:
        right_up["rank"] = len(gv.processed_images) + 1
        gv.processed_images.append(right_up_filename)
    left_down=gv.file_map[left_down_filename]
    left_down_row,left_down_col=left_down["row"],left_down["col"]
    right_up_row,right_up_col=right_up["row"],right_up["col"]
    #然后开始寻找新的右下图
    if left_down_row-right_up_row==2 and right_up_col-left_down_col>1: #说明此时需要转到compare_three_images来处理
        up_filename=gv.matrix[right_up_row][right_up_col+1]
        middle_filename=gv.matrix[right_up_row+1][right_up_col]
        down_filename=left_down_filename
        gv.compare_group = {"group": [up_filename, middle_filename, down_filename]}
        return compare_three_images(gv)
    if right_up_col + 1 < len(gv.matrix[right_up_row]):
        right_up["col"] += 1  # 右上图片右移一格
        new_right_up_filename = gv.matrix[right_up["row"]][right_up["col"]]
        gv.compare_group = {"group": [left_down_filename, new_right_up_filename]}
        return compare_two_images(gv)
    else:  # 说明right_up需要换行，此时left_down成为了未经过比较的图片组成的矩阵的左上角
        if left_down["row"]==len(gv.matrix)-1: #那么left_down在最后一行，说明此矩阵中的图片排序结束，可以进行对下一个矩阵的处理
            last_left_down(gv)
            gv.compare_group=None

            result = matrix_for_compare(gv)
            print(f"find_right_up 返回(end): {result}")
            print("请再次调用'compare_group'接口")
            return result
        if left_down_filename not in gv.processed_images:
            left_down["rank"] = len(gv.processed_images) + 1
            gv.processed_images.append(left_down_filename)
        right_up_filename=gv.matrix[left_down_row][left_down_col+1]
        left_down["row"]+=1
        new_left_down_filename = gv.matrix[left_down["row"]][left_down["col"]]
        gv.compare_group = {"group": [new_left_down_filename, right_up_filename]}
        return compare_two_images(gv)

def compare_two_images(gv:Variable):
    left_down_filename=None
    right_up_filename=None
    left_down=None
    right_up=None
    if gv.compare_group: #查询compare_group状态，若不为空则开始进行更新
        left_down_filename, right_up_filename = left_or_right(gv)
        left_down = gv.file_map[left_down_filename]
        right_up = gv.file_map[right_up_filename]
    elif gv.first_compare: #说明需要进行故障排查
        return {"message:":"compare_group缺失，请重新确认状态。"}
    else: #说明是首次进行比较，需先初始化状态
        set_start(gv)
        compare_two_images(gv)
    if None not in [left_down_filename,right_up_filename,left_down,right_up]:
        if left_down_filename in right_up["superior"]:  # 说明需要寻找新的right_up
            return find_right_up(gv, left_down_filename, right_up_filename)
        elif right_up_filename in left_down["superior"]:  # 说明需要寻找新的left_down
            return find_left_down(gv, left_down_filename, right_up_filename)
        else:
            return [left_down_filename, right_up_filename]
    else: #说明局部变量状态设置出错
        return {"message:":"局部变量引用出错，请重新检查"}

def find_up(gv:Variable,up_filename, middle_filename, down_filename):
    if not hasattr(gv, 'search_path'):
        gv.search_path = []
    current_pair = (up_filename, middle_filename, down_filename)
    gv.search_path.append(current_pair)
    print(f"🔍 搜索路径: {' -> '.join([f'{a}>{b}and{c}' for a, b,c in gv.search_path])}")
    #先设置好有关up的一切
    up=gv.file_map[up_filename]
    if up_filename not in gv.processed_images:
        up["rank"] = len(gv.processed_images) + 1
        gv.processed_images.append(up_filename)
    up_row,up_col=up["row"],up["col"]
    #然后寻找三张图片中的上位
    if up_col + 1 < len(gv.matrix[up_row]):
        up["col"] += 1  # 右上图片右移一格
        new_up_filename = gv.matrix[up["row"]][up["col"]]
        gv.compare_group = {"group": [new_up_filename,middle_filename,down_filename]}
        return compare_three_images(gv)
    else: #说明不复存在up
        gv.compare_group = {"group": [middle_filename, down_filename]}
        return compare_two_images(gv)

def find_middle(gv:Variable,up_filename, middle_filename, down_filename):
    if not hasattr(gv, 'search_path'):
        gv.search_path = []
    current_pair = (up_filename, middle_filename, down_filename)
    gv.search_path.append(current_pair)
    print(f"🔍 搜索路径: {' -> '.join([f'{b}>{a}and{c}' for a, b,c in gv.search_path])}")
    #准备工作与find_up同理
    up = gv.file_map[up_filename]
    middle = gv.file_map[middle_filename]
    if middle_filename not in gv.processed_images:
        middle["rank"] = len(gv.processed_images) + 1
        gv.processed_images.append(middle_filename)
    # 用于寻找三张图片中的中位
    if middle["col"]+1<up["col"]:
        middle["col"]+=1
        new_middle_filename = gv.matrix[middle["row"]][middle["col"]]
        gv.compare_group = {"group": [up_filename, new_middle_filename, down_filename]}
        return compare_three_images(gv)
    else: #说明可以直接比较up和down
        gv.compare_group = {"group": [up_filename, middle_filename, down_filename]}
        return compare_two_images(gv)

def find_down(gv:Variable,up_filename, middle_filename, down_filename):
    if not hasattr(gv, 'search_path'):
        gv.search_path = []
    current_pair = (up_filename, middle_filename, down_filename)
    gv.search_path.append(current_pair)
    print(f"🔍 搜索路径: {' -> '.join([f'{c}>{b}and{a}' for a, b,c in gv.search_path])}")
    #用于寻找三张图片中的下位
    middle = gv.file_map[middle_filename]
    down = gv.file_map[down_filename]
    if down_filename not in gv.processed_images:
        down["rank"] = len(gv.processed_images) + 1
        gv.processed_images.append(down_filename)
    if down["col"]+1<middle["col"]:
        down["col"]+=1
        new_down_filename = gv.matrix[down["row"]][down["col"]]
        gv.compare_group = {"group": [up_filename, middle_filename, new_down_filename]}
        return compare_three_images(gv)
    else: #说明可以直接比较up和middle
        gv.compare_group = {"group": [up_filename, middle_filename, down_filename]}
        return compare_two_images(gv)

def double_check_for_three(gv:Variable,up_filename, middle_filename, down_filename):
    #对compare_three_images()进行的二次检查，以确保每个特殊情况都能够得到处理
    up = gv.file_map[up_filename]
    middle = gv.file_map[middle_filename]
    down = gv.file_map[down_filename]
    if down_filename in up["superior"]:
        gv.compare_group = {"group": [up_filename, middle_filename]}
        return compare_two_images(gv)
    if middle_filename in up["superior"]:
        gv.compare_group = {"group": [up_filename,down_filename]}
        return compare_two_images(gv)
    if up_filename in middle["superior"]:
        gv.compare_group = {"group": [middle_filename, down_filename]}
        return compare_two_images(gv)
    if down_filename in middle["superior"]:
        gv.compare_group = {"group": [middle_filename, up_filename]}
        return compare_two_images(gv)
    if up_filename in down["superior"]:
        gv.compare_group = {"group": [down_filename,middle_filename]}
        return compare_two_images(gv)
    if middle_filename in down["superior"]:
        gv.compare_group = {"group": [down_filename, up_filename]}
        return compare_two_images(gv)
    gv.compare_group = {"group": [up_filename, middle_filename, down_filename]}
    return [up_filename, middle_filename, down_filename]

def compare_three_images(gv:Variable):
    #用于处理compare_two_images中出现的特殊情况（即需要比较三张图片的情况
    up_filename,middle_filename,down_filename=gv.compare_group["group"]
    up = gv.file_map[up_filename]
    middle = gv.file_map[middle_filename]
    down = gv.file_map[down_filename]
    if down_filename in up["superior"] and middle_filename in up["superior"]:  # 说明需要寻找新的up
        return find_up(gv,up_filename, middle_filename, down_filename)
    elif up_filename in middle["superior"] and down_filename in middle["superior"]:#说明需要寻找新的middle
        return find_middle(gv,up_filename, middle_filename, down_filename)
    elif up_filename in down["superior"] and middle_filename in down["superior"]:  # 说明需要寻找新的down
        return find_down(gv,up_filename, middle_filename, down_filename)
    else:
        return double_check_for_three(gv,up_filename, middle_filename, down_filename)

def compare_images_logic(gv:Variable):
    #前置防御🛡️
    if not gv.compare_group:
        set_start(gv)
        if not gv.compare_group: #进行二次检查，防止错误数据流入
            return ValueError("当前矩阵已处理完毕")
    group=gv.compare_group.get("group")
    if not group:
        raise RuntimeError(
            "compare_images(): 'compare_group' 结构出错"
        )
    # 寻找需要进行比较的图片对
    if len(group) == 2:
        compare_group=compare_two_images(gv)
        if compare_group: #正常情况下
            return compare_group
        else: #说明这一组矩阵已完成比较，可自动进入对下一组矩阵的处理
            return None
    elif len(group) == 3:
        compare_group=compare_three_images(gv)
        if compare_group:
            return compare_group
        else:
            return None
    else:
        raise RuntimeError("compare_group长度非法")
@app.get("/images/compare")
def compare_images(request:Request):
    task_id = request.headers.get("X-Task-ID")
    gv = get_task(task_id)
    result=compare_images_logic(gv)
    if result:
        return {"group":result}
    #没有返回结果的话就说明当前matrix已处理完成
    gv.first_compare = None  # 以重新设置矩阵中首组图片状态
    gv.matrix = None
    update_superior(gv, gv.processed_images)
    # 检查是否还有下一个 matrix
    next_small_matrix(gv)
    if gv.matrix_queue or gv.current_matrix: #如果有，那么就为进入下一轮matrix的比较做准备
        gv.final_matrix.append(gv.processed_images.copy())
        gv.processed_images = []
        get_matrix(gv)
        get_coordinate(gv)
        set_start(gv)
        return compare_images_logic(gv)
    gv.matrix_queue = None  # 清空队列
    if gv.stage=="final":
        gv.final_matrix=list(set(gv.processed_images.copy())) #拷贝最终全局图片排序关系
        return None
    else: #说明所有的矩阵已都被处理完毕，可进行对final_matrix的处理
        # 如果有 rows，合并
        if gv.rows:
            merge_rows_into_final_matrix(gv)
        get_coordinate(gv)
        set_start(gv)
        return compare_images_logic(gv)

def merge_rows_into_final_matrix(gv:Variable):
    #将rows合并到final_matrix中，并将final_matrix当做matrix以进行最后一次全局排序
    if gv.rows:
        for row in gv.rows:
            gv.final_matrix.append(row)
        gv.rows=None
    gv.matrix = gv.final_matrix.copy()
    gv.final_matrix = None
    gv.stage="final" #用于提示程序已进入最终矩阵处理阶段，防止与matrices中的每一个matrix的阶段性结束混淆

@app.get("/images/result")
def final_result(request:Request):
    #获取最终的全局排序，任务结束！
    task_id = request.headers.get("X-Task-ID")
    gv = get_task(task_id)
    final_order=gv.processed_images
    gv.processed_images = None
    if gv.matrix_queue or gv.matrix or gv.rows:
        raise HTTPException(status_code=400,detail="NOT YET！")
    print("superior 关系:")
    for img in gv.uploaded_files:
        print(f"{img['filename']}: superior={img['superior']}")
    return {"final_order":final_order}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)