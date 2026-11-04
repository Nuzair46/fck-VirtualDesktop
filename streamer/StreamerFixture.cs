// Test-only metadata rewrites. Outputs are never executed or installed.
using System;
using System.IO;
using System.Linq;
using dnlib.DotNet;
using dnlib.DotNet.Emit;
using dnlib.DotNet.Writer;

public static class StreamerFixture {
    public static string FixtureVersion() { return "9.8.7"; }
    public static string EvaluateFooter(string input) {
        using(var m=ModuleDefMD.Load(input)){
            var method=m.GetTypes().Single(t=>t.FullName=="VirtualDesktop.Streamer.UISettings").Methods.Single(x=>x.Name=="get_ProductVersion");
            var instructions=method.Body.Instructions;
            if(instructions.Count!=4 || instructions[0].OpCode!=OpCodes.Call || instructions[1].OpCode!=OpCodes.Ldstr || instructions[2].OpCode!=OpCodes.Call || instructions[3].OpCode!=OpCodes.Ret)throw new InvalidDataException("Unexpected footer IL shape");
            if(((IMethod)instructions[2].Operand).FullName!="System.String System.String::Concat(System.String,System.String)")throw new InvalidDataException("Unexpected footer concat method");
            // Execute the emitted four-instruction recipe in an owned dynamic
            // method. Only the vendor version formatter is replaced by a stub;
            // no vendor assembly is loaded into the CLR or executed.
            var test=new System.Reflection.Emit.DynamicMethod("OwnedFooterFixture",typeof(string),Type.EmptyTypes);
            var il=test.GetILGenerator();
            il.Emit(System.Reflection.Emit.OpCodes.Call,typeof(StreamerFixture).GetMethod("FixtureVersion"));
            il.Emit(System.Reflection.Emit.OpCodes.Ldstr,(string)instructions[1].Operand);
            il.Emit(System.Reflection.Emit.OpCodes.Call,typeof(string).GetMethod("Concat",new[]{typeof(string),typeof(string)}));
            il.Emit(System.Reflection.Emit.OpCodes.Ret);
            return (string)test.Invoke(null,null);
        }
    }
    public static void Create(string input,string output,string mode) {
        if(File.Exists(output))throw new IOException("Fixture output must be new");
        using(var m=ModuleDefMD.Load(input)){
            foreach(var method in m.GetTypes().SelectMany(t=>t.Methods).Where(x=>x.HasBody))method.Body.KeepOldMaxStack=true;
            if(mode=="layout"){
                var type=new TypeDefUser("LocalProfileTest","UnrelatedMarker",m.CorLibTypes.Object.TypeDefOrRef);
                var method=new MethodDefUser("UnrelatedNoop",MethodSig.CreateStatic(m.CorLibTypes.Void),MethodImplAttributes.IL|MethodImplAttributes.Managed,MethodAttributes.Public|MethodAttributes.Static);
                method.Body=new CilBody();method.Body.Instructions.Add(Instruction.Create(OpCodes.Ret));
                type.Methods.Add(method);m.Types.Insert(1,type);
            }else if(mode=="encoding"){
                foreach(var method in m.GetTypes().SelectMany(t=>t.Methods).Where(x=>x.HasBody))method.Body.SimplifyMacros(method.Parameters);
            }else if(mode=="ambiguous"){
                var type=m.GetTypes().Single(x=>x.FullName=="VirtualDesktop.Streamer.UpdateHelper");
                var original=type.Methods.Single(x=>x.MDToken.Raw==0x0600051f);
                var method=new MethodDefUser(original.Name,original.MethodSig,original.ImplAttributes,original.Attributes);
                method.Body=new CilBody();method.Body.Instructions.Add(Instruction.Create(OpCodes.Ldnull));method.Body.Instructions.Add(Instruction.Create(OpCodes.Ret));
                type.Methods.Add(method);
            }else throw new ArgumentException("Unknown fixture mode");
            var options=new ModuleWriterOptions(m);
            if(mode!="layout")options.MetadataOptions.Flags=MetadataFlags.PreserveAll;
            m.Write(output,options);
        }
    }
}
