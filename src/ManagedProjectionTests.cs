// Evaluates only the inserted guard's IL against owned dictionary fixtures.
// Never loads or executes any vendor assembly or original application method.
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using dnlib.DotNet;
using dnlib.DotNet.Emit;

internal static class ManagedProjectionTests {
    static void Check(bool ok,string message) {if(!ok)throw new InvalidDataException(message);}
    static Dictionary<string,object> Vector(params float[] values) {
        var result=new Dictionary<string,object>();
        for(int i=0;i<values.Length;i++)result.Add(new[]{"X","Y","Z"}[i],values[i]);
        return result;
    }
    static Dictionary<string,object> State(float width,float height) {
        var matrix=new Dictionary<string,object>();
        for(int row=1;row<=4;row++)for(int col=1;col<=4;col++)matrix.Add("M"+row+col,0f);
        matrix["M33"]=1.1111112f;matrix["M43"]=1f;matrix["M44"]=1f;
        return new Dictionary<string,object>{{"_realResolution",Vector(width,height)},
            {"_virtualResolution",Vector(width,height,1f)},
            {"_renderingContext",new Dictionary<string,object>{{"ViewProjectionMatrix",matrix}}}};
    }
    static Dictionary<string,object> Child(Dictionary<string,object> state,string key) {return (Dictionary<string,object>)state[key];}
    static Dictionary<string,object> Matrix(Dictionary<string,object> state) {return Child(Child(state,"_renderingContext"),"ViewProjectionMatrix");}
    static void Evaluate(Instruction[] guard,Instruction resume,Dictionary<string,object> state) {
        var stack=new Stack<object>();
        foreach(var ins in guard) {
            switch(ins.OpCode.Code) {
                case Code.Ldarg_0:stack.Push(state);break;
                case Code.Ldfld:case Code.Ldflda:
                    stack.Push(((Dictionary<string,object>)stack.Pop())[((IField)ins.Operand).Name]);break;
                case Code.Ldc_R4:stack.Push((float)ins.Operand);break;
                case Code.Div: {float right=(float)stack.Pop(),left=(float)stack.Pop();stack.Push(left/right);break;}
                case Code.Stfld: {var value=stack.Pop();((Dictionary<string,object>)stack.Pop())[((IField)ins.Operand).Name]=value;break;}
                case Code.Blt_Un:case Code.Bgt_Un:case Code.Ble_Un:case Code.Bge_Un:case Code.Bne_Un: {
                    Check(ins.Operand==resume,"Guard branches outside its resume point");
                    float right=(float)stack.Pop(),left=(float)stack.Pop();
                    bool take=Single.IsNaN(left)||Single.IsNaN(right);
                    switch(ins.OpCode.Code) {
                        case Code.Blt_Un:take|=left<right;break;
                        case Code.Bgt_Un:take|=left>right;break;
                        case Code.Ble_Un:take|=left<=right;break;
                        case Code.Bge_Un:take|=left>=right;break;
                        case Code.Bne_Un:take|=left!=right;break;
                    }
                    if(take){Check(stack.Count==0,"Guard branch leaves a nonempty stack");return;}
                    break;
                }
                default:throw new InvalidDataException("Unsupported emitted guard opcode: "+ins.OpCode);
            }
        }
        Check(stack.Count==0,"Guard leaves a nonempty stack");
    }
    static void Case(Instruction[] guard,Instruction resume,string name,Dictionary<string,object> state,bool repair) {
        var matrix=Matrix(state);var before=matrix.ToDictionary(x=>x.Key,x=>(float)x.Value);
        Evaluate(guard,resume,state);
        foreach(var pair in before) {
            float expected=pair.Value;
            if(repair && pair.Key=="M11")expected=2f/(float)Child(state,"_virtualResolution")["X"];
            if(repair && pair.Key=="M22")expected=-2f/(float)Child(state,"_virtualResolution")["Y"];
            Check(BitConverter.ToInt32(BitConverter.GetBytes((float)matrix[pair.Key]),0)==BitConverter.ToInt32(BitConverter.GetBytes(expected),0),name+" changed wrong field/value: "+pair.Key);
        }
    }
    public static int Main(string[] args) {
        try {
            Check(args.Length==2,"ORIGINAL_MOBILE PATCHED_MOBILE required");
            using(var original=ModuleDefMD.Load(args[0]))using(var patched=ModuleDefMD.Load(args[1])) {
                Check(original.Mvid==patched.Mvid && original.Assembly.Version==patched.Assembly.Version,"Assembly identity changed");
                var owner=patched.GetTypes().Single(x=>x.FullName=="VirtualDesktop.Mobile.UIGameSystem");
                var method=owner.Methods.Single(x=>x.Name=="LoadPage");
                var address=method.Body.Instructions.First(x=>x.OpCode==OpCodes.Ldflda && x.Operand is IField && ((IField)x.Operand).Name=="ViewProjectionMatrix");
                int start=method.Body.Instructions.IndexOf(address)+2;
                var resume=(Instruction)method.Body.Instructions.Skip(start).First(x=>x.Operand is Instruction).Operand;
                int length=method.Body.Instructions.IndexOf(resume)-start;
                Check(length>100,"Inserted guard not found");
                var guard=method.Body.Instructions.Skip(start).Take(length).ToArray();
                var stores=guard.Where(x=>x.OpCode==OpCodes.Stfld).Select(x=>((IField)x.Operand).Name.String).ToArray();
                Check(stores.SequenceEqual(new[]{"M11","M22"}),"Guard writes unexpected fields");
                Check(!guard.Any(x=>x.OpCode==OpCodes.Call || x.OpCode==OpCodes.Callvirt),"Guard introduced calls");
                int cases=0;
                foreach(var size in new[]{new[]{1280f,1080f},new[]{1f,1f},new[]{4096f,4096f},new[]{1032f,128f}}) {Case(guard,resume,"observed/bounds",State(size[0],size[1]),true);cases++;}
                foreach(string field in Matrix(State(1280,1080)).Keys)foreach(float value in new[]{Single.NaN,Single.PositiveInfinity,Single.NegativeInfinity,7f}) {
                    var state=State(1280,1080);Matrix(state)[field]=value;Case(guard,resume,"matrix "+field,state,false);cases++;
                }
                foreach(string ownerName in new[]{"_realResolution","_virtualResolution"})foreach(string field in new[]{"X","Y"})foreach(float value in new[]{Single.NaN,Single.PositiveInfinity,Single.NegativeInfinity,0f,-1f,4097f}) {
                    var state=State(1280,1080);Child(state,ownerName)[field]=value;Case(guard,resume,"resolution "+ownerName+field,state,false);cases++;
                }
                foreach(float value in new[]{Single.NaN,Single.PositiveInfinity,0f,2f}) {
                    var state=State(1280,1080);Child(state,"_virtualResolution")["Z"]=value;Case(guard,resume,"depth",state,false);cases++;
                }
                var healthy=State(1280,1080);Matrix(healthy)["M11"]=0.0015625f;Matrix(healthy)["M22"]=-0.0018518518f;Case(guard,resume,"healthy projection",healthy,false);cases++;
                foreach(var edge in new[]{new object[]{"M33",1.11110f},new object[]{"M33",1.11112f},new object[]{"M43",0.99999f},new object[]{"M43",1.00001f}}) {
                    var state=State(1280,1080);Matrix(state)[(string)edge[0]]=edge[1];Case(guard,resume,"exclusive tolerance",state,false);cases++;
                }
                for(int i=0;i<length;i++)method.Body.Instructions.RemoveAt(start);
                var originalLoad=original.GetTypes().Single(x=>x.FullName==owner.FullName).Methods.Single(x=>x.Name=="LoadPage");
                Check(ManagedProfile.Fingerprint(method)==ManagedProfile.Fingerprint(originalLoad),"Original projection method changed outside inline guard");
                var footer=patched.GetTypes().Single(x=>x.FullName=="VirtualDesktop.Mobile.BottomPanel").Methods.Single(x=>x.IsInstanceConstructor);
                var suffix=footer.Body.Instructions.Single(x=>x.OpCode==OpCodes.Ldstr && (string)x.Operand==" fkvd");
                int suffixIndex=footer.Body.Instructions.IndexOf(suffix);
                Check(((IMethod)footer.Body.Instructions[suffixIndex+1].Operand).FullName=="System.String System.String::Concat(System.String,System.String)","Footer suffix call differs");
                footer.Body.Instructions.RemoveAt(suffixIndex);footer.Body.Instructions.RemoveAt(suffixIndex);
                var originalFooter=original.GetTypes().Single(x=>x.FullName==footer.DeclaringType.FullName).Methods.Single(x=>x.IsInstanceConstructor);
                Check(ManagedProfile.Fingerprint(footer)==ManagedProfile.Fingerprint(originalFooter),"Footer changed outside suffix insertion");
                foreach(var m in original.GetTypes().Where(x=>x.FullName=="VirtualDesktop.Mobile.DynamicSettings").SelectMany(x=>x.Methods).Where(x=>x.Name=="get_VersionString" || x.Name=="set_VersionString"))
                    Check(ManagedProfile.Fingerprint(m)==ManagedProfile.Fingerprint(patched.ResolveMethod(m.Rid)),"Stored version accessor changed");
                Console.WriteLine("Guard fixtures passed: "+cases+"; original LoadPage/footer semantics restored after removing exact insertions; identity and version accessors preserved.");
            }
            return 0;
        }catch(Exception error){Console.Error.WriteLine(error);return 1;}
    }
}
